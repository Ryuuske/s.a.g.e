#!/usr/bin/env python3
"""SessionStart hook: inject an autonomous-run continuation pointer.

Phase-5 autonomy engine, ADR-0066 sub-decision B1. When a multi-phase
autonomous run is in-flight, this hook injects a one-line re-anchor pointer
so a fresh session (after a compaction or a session restart) self-continues
from the durable run-log instead of waiting for the User.

This is a fail-open CONTEXT-INJECTOR, never an enforcer (ADR-0011). It prints
a pointer to stdout (which the Claude Code harness reads into context) and
returns 0 on every path. It never blocks session start, never reads or alters
output, and never forces a skill reload (no hook-level skill-reload primitive
exists — it surfaces a `/reload-plugins` reminder instead).

Contract — the orchestrator owns one marker file:

  ~/.sage/autonomy-run.json
      Written by the orchestrator when an autonomous run starts; updated at
      each phase; cleared (deleted) at the terminal. JSON object:
        {
          "run_log": "<abs path to .development/handoff/<run>-run-log.md>",
          "phase":   "<current phase id, e.g. '6'>",
          "status":  "in-flight" | "terminal",
          "skills_changed": true | false   # optional; set when the run
                                            # edited skills since last reload
        }
      Absent file, malformed JSON, or status != "in-flight" => the hook is
      silent (no-op). The hook NEVER infers a run from cwd.

Mirrors the fail-open philosophy of inject-codex-budget.py and
claude-wakeup-sessionstart.py: every error path returns 0; silent when the
marker is absent.
"""

from __future__ import annotations

import json
import os
import stat as _stat
import time
from pathlib import Path

# ── SAGE_HOOK_PROFILE kill-switch ─────────────────────────────────────────────
# Mirror of the same helper in _session_hook.py and autonomy_reporting_eval.py.
# Do NOT import from the sage package here; this script runs as a standalone
# SessionStart hook where the package / venv may be absent.
# Values: off / minimal / standard; unset or unknown → standard; fail-open.
_HOOK_PROFILE_ENV = "SAGE_HOOK_PROFILE"
_HOOK_PROFILE_OFF = "off"


def _read_hook_profile() -> str:
    """Return the active SAGE_HOOK_PROFILE value, resolved from the environment.

    Unset or unrecognised values resolve to 'standard' so the dial is opt-in.
    Fails open (returns 'standard') on any error so the hook never crashes.
    """
    try:
        raw = os.environ.get(_HOOK_PROFILE_ENV, "").strip().lower()
        if raw in ("off", "minimal", "standard"):
            return raw
    except Exception:  # noqa: BLE001 — fail open
        pass
    return "standard"


_MARKER = Path.home() / ".sage" / "autonomy-run.json"

# A marker not updated within this window is treated as stale (a crashed run
# that never cleared its marker, or an abandoned one) and ignored — so a stale
# in-flight marker cannot hijack every future session. The orchestrator
# re-touches the marker each phase, so a live run stays fresh.
_STALE_SECONDS = 7 * 24 * 3600  # 7 days
_MAX_RUNLOG_BYTES = 4 * 1024 * 1024  # 4 MiB ceiling on a plausible run-log


def _load_marker() -> dict | None:
    """Return the parsed marker dict, or None on any absence/parse error."""
    try:
        raw = _MARKER.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def main() -> int:
    # SAGE_HOOK_PROFILE kill-switch — checked before any stdout write.
    # 'off' means all hook side-effects no-op; return 0, emit nothing.
    if _read_hook_profile() == _HOOK_PROFILE_OFF:
        return 0

    # Fail-open at the outermost layer: any unexpected error returns 0 so the
    # hook can never block session start.
    try:
        data = _load_marker()
        if data is None:
            return 0
        if data.get("status") != "in-flight":
            return 0

        # Staleness guard: a marker not updated within the window is a crashed /
        # abandoned run — ignore it so it cannot hijack every future session.
        try:
            if (time.time() - _MARKER.stat().st_mtime) > _STALE_SECONDS:
                return 0
        except OSError:
            return 0

        run_log = str(data.get("run_log") or "").strip()
        phase = str(data.get("phase") or "?").strip()
        skills_changed = bool(data.get("skills_changed"))

        # Validate run_log is a plausible path (absolute, bounded, .md, single
        # line) AND an EXISTING regular bounded file. If it cannot be confirmed
        # as a real run-log, no-op entirely — never emit a CONTINUE instruction
        # for an unverifiable run (a deleted run-log, a wrong-repo/wrong-machine
        # marker, or a corrupt path). This is the primary defence against a stale
        # marker misdirecting a fresh session.
        if not (
            run_log.startswith("/")
            and len(run_log) <= 512
            and run_log.endswith(".md")
            and "\n" not in run_log
        ):
            return 0
        try:
            st = Path(run_log).stat()
            if not _stat.S_ISREG(st.st_mode) or st.st_size > _MAX_RUNLOG_BYTES:
                return 0
        except OSError:
            return 0
        # Phase is a short id; bound it too so a crafted marker cannot inject prose.
        if len(phase) > 32 or "\n" in phase:
            phase = "?"

        # The pointer is intentionally one line of guidance plus the validated path.
        print("## AUTONOMY RUN IN-FLIGHT (sage session start)")
        print(
            f"An approved autonomous run is in-flight at phase {phase}. "
            f"Re-anchor from the run-log and CONTINUE without waiting for the User: "
            f"read {run_log} + `git log`/`git status` (git is truth on conflict), "
            f"identify the current phase, and resume the autonomy-loop at the recorded step."
        )
        if skills_changed:
            print(
                "## note: skills changed this run — run `/reload-plugins` so the latest "
                "skills are in effect before continuing (no hook-level reload exists)."
            )
    except Exception:
        # Never let a continuation injector break session start.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
