#!/usr/bin/env python3
"""Stop hook: non-blocking reporting-contract evaluator (autonomy runs).

Phase-5 autonomy engine, ADR-0066 sub-decision A2. Records post-hoc whether
an in-flight autonomous run's output followed the card format (the reporting
contract that is a REQUIRED property of skills/autonomy-loop/SKILL.md), for
later review.

This is an EVALUATOR, not an ENFORCER (ADR-0011): it reads the durable
run-log (where the cards are recorded), counts the card markers, appends a
conformance line to a log, and returns 0 on every path. It NEVER alters or
blocks output, NEVER cancels the Stop, and is silent when no autonomous run
is in-flight. There is no Claude Code per-message ("MessageDisplay") hook
event; recording at Stop against the durable run-log is the ADR-0011-compatible
mechanical support for the reporting discipline.

Contract — reads the orchestrator-owned marker the SessionStart continuation
injector also reads:

  ~/.sage/autonomy-run.json   (see autonomy-continuation-sessionstart.py)
      Provides "run_log": the path whose cards are evaluated. Absent /
      malformed / status != "in-flight" => silent no-op.

Writes (append-only, best-effort):

  ~/.sage/autonomy-reporting-eval.log
      One line per evaluation: counts of START / STATUS / DECISION /
      PHASE-COMPLETE card markers found in the run-log. For review only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# ── SAGE_HOOK_PROFILE kill-switch ─────────────────────────────────────────────
# Mirror of the same helper in _session_hook.py — do NOT import from the sage
# package or from _session_hook here; this script runs as a standalone Stop
# hook in the installed-hook context where the package / venv may be absent.
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
_LOG = Path.home() / ".sage" / "autonomy-reporting-eval.log"

# Card markers per the reporting contract (skills/autonomy-loop/SKILL.md).
# Substrings, matched case-insensitively — the cards are prose, not a strict
# grammar, so this is a presence/count recorder, not a validator.
_CARD_MARKERS = {
    "start": "START",  # START card opens each phase (distinct from COMPLETE)
    "status": "STATUS",  # STATUS card at each milestone
    "decision": "DECISION",  # DECISION card per arbiter ruling
    "phase_complete": "COMPLETE",  # PHASE COMPLETE card closes each phase
}

# Bound the run-log read so a pathological run_log (a FIFO, /dev/zero, or a
# huge file) cannot hang or balloon the Stop hook. The harness timeout is the
# hard backstop; this is the fail-closed-to-silent soft bound.
_MAX_RUNLOG_BYTES = 2 * 1024 * 1024  # 2 MiB is far above any real run-log


def _load_marker() -> dict | None:
    try:
        raw = _MARKER.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def main() -> int:
    # A Stop hook must NEVER block the Stop; swallow every error and return 0.
    try:
        # SAGE_HOOK_PROFILE=off silences all sage hook side-effects; no log write.
        if _read_hook_profile() == _HOOK_PROFILE_OFF:
            return 0

        data = _load_marker()
        if data is None or data.get("status") != "in-flight":
            return 0

        run_log = str(data.get("run_log") or "").strip()
        if not run_log:
            return 0
        try:
            p = Path(run_log)
            # Skip anything that is not a plain regular file (FIFO, device,
            # socket, dir) or is implausibly large — fail closed-to-silent.
            st = p.stat()
            import stat as _stat

            if not _stat.S_ISREG(st.st_mode) or st.st_size > _MAX_RUNLOG_BYTES:
                return 0
            with p.open("r", encoding="utf-8") as fh:
                text = fh.read(_MAX_RUNLOG_BYTES)
        except OSError:
            return 0

        upper = text.upper()
        counts = {name: upper.count(marker.upper()) for name, marker in _CARD_MARKERS.items()}
        phase = str(data.get("phase") or "?").strip()
        line = f"phase={phase} " + " ".join(f"{name}={n}" for name, n in counts.items()) + "\n"
        try:
            with _LOG.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            return 0
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
