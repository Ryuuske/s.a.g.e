#!/usr/bin/env python3
"""PreCompact hook for the sage Claude Code plugin.

Invoked by Claude Code before it compacts context. Delegates to
``_session_hook.run_session_hook`` (shared with stop.py) so the
wing-detection contract and the Keeper skip-check stay in one
place; see that module's docstring for the full contract.

CRITICAL: this hook must NEVER cancel compaction. A hook exception
that propagates up would cancel the compact, losing the session; the
shared body swallows every exception and always returns 0 so
compaction continues even if the drawer write fails.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from _session_hook import run_session_hook
    except Exception as exc:
        print(f"precompact hook: import failed (fail-open): {exc}", file=sys.stderr)
        return 0

    return run_session_hook(room="handoff-precompact", agent_tag="pre-compact-hook")


if __name__ == "__main__":
    sys.exit(main())
