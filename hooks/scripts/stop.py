#!/usr/bin/env python3
"""Stop hook for the sage Claude Code plugin.

Invoked by Claude Code at session end. Delegates to
``_session_hook.run_session_hook`` so the wing-detection contract and
the Keeper skip-check stay in one place; see that module's
docstring for the full contract.

This hook does NOT auto-run ``sage mine`` on the transcript
directory: auto-mining the whole transcript dir blows up into a
giant pile of unrelated content with no opt-out. When a wing is
advertised and the Keeper has not dispatched recently, the hook
files exactly one drawer carrying the last 4000 characters of the
session.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    # Ensure the shared body is importable when the hook is invoked from
    # the plugin path (Claude Code runs it as a standalone script).
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from _session_hook import run_session_hook
    except Exception as exc:
        print(f"stop hook: import failed (fail-open): {exc}", file=sys.stderr)
        return 0

    return run_session_hook(room="handoff", agent_tag="session-end-hook")


if __name__ == "__main__":
    sys.exit(main())
