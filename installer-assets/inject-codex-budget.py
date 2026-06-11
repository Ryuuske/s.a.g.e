#!/usr/bin/env python3
"""SessionStart hook: inject a one-line Codex budget summary into context.

Runs once per Claude Code session. Reads ~/.cache/sage/codex.json
if fresh; otherwise spawns `codex app-server` to refresh.

Output is intentionally terse — every byte costs context every session.
Silent if Codex is not configured (FileNotFoundError on the binary).

Timeout chain (no hard signal.alarm — see M7 audit note):
  - settings.json hook budget: 10 s (Claude Code kills the hook if exceeded).
  - codex_window.fetch_live recv timeout: 8 s (set inside codex_window.py).
  - _spawn_app_server cold-start overhead: ~1-2 s on first call.
  - Net worst-case: ~10-11 s, which is at the edge of the hook budget.
  If a hard timeout is needed in future, wrap get_budget() in
  signal.alarm(9) (POSIX only) and catch signal.Alarm in main().

SAGE_HOOK_PROFILE=off silences all sage hook side-effects (kill-switch).
When off, this injector emits nothing and exits 0 cleanly.
minimal/standard/unset → proceed (inject as normal).
"""

from __future__ import annotations

import os
import sys
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


HOOK_DIR = Path(__file__).resolve().parent
CANDIDATE_LIB_DIRS = [
    HOOK_DIR.parent / "statusline" / "lib",
    Path.home() / ".claude" / "statusline" / "lib",
    Path("/opt/sage/statusline/lib"),
]

for candidate in CANDIDATE_LIB_DIRS:
    if (candidate / "codex_window.py").exists():
        sys.path.insert(0, str(candidate))
        break


def format_reset(epoch: int | None) -> str:
    if not epoch:
        return "—"
    import time
    from format import format_duration  # type: ignore

    return format_duration(int(epoch) - time.time())


def main() -> int:
    # SAGE_HOOK_PROFILE kill-switch — checked before any stdout write.
    # 'off' means all hook side-effects no-op; return 0, emit nothing.
    if _read_hook_profile() == _HOOK_PROFILE_OFF:
        return 0

    try:
        from codex_window import get_budget  # type: ignore
    except ImportError:
        print(
            "inject-codex-budget: codex_window not found in any CANDIDATE_LIB_DIRS — sage may not be fully installed",
            file=sys.stderr,
        )
        return 0

    try:
        budget = get_budget()
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        # Fail open: hook must never block Claude Code's session start.
        print(f"inject-codex-budget: {exc}", file=sys.stderr)
        return 0

    primary = budget.get("primary") or {}
    secondary = budget.get("secondary") or {}
    p_pct = primary.get("used_percent")
    w_pct = secondary.get("used_percent")
    if p_pct is None and w_pct is None:
        return 0

    try:
        plan = budget.get("plan_type") or "unknown"
        parts: list[str] = ["codex_budget", f"plan={plan}"]
        if p_pct is not None:
            parts.append(f"5h={int(round(p_pct))}%/{format_reset(primary.get('resets_at'))}")
        if w_pct is not None:
            parts.append(f"weekly={int(round(w_pct))}%/{format_reset(secondary.get('resets_at'))}")
        if budget.get("stale"):
            parts.append("stale=true")
        if budget.get("rate_limit_reached_type"):
            parts.append(f"reached={budget['rate_limit_reached_type']}")

        print(" ".join(parts))
    except (TypeError, ValueError, ImportError):
        # Malformed cache values or a missing format.py in a partial install
        # must not crash the hook. Silent per docstring: "Silent if Codex is
        # not configured." ImportError covers format_duration unavailable when
        # codex_window.py resolved via one CANDIDATE_LIB_DIRS path but
        # format.py is absent at that same path.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
