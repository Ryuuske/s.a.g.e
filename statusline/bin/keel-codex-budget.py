#!/usr/bin/env python3
"""Print the current Codex rate-limit budget as JSON.

Used by the orchestrator (via the codex-budget skill) before invoking
/codex:* commands. Reads from the cache if fresh; otherwise spawns
`codex app-server` to refresh.

Flags:
  --refresh   bypass cache and resample
  --pretty    pretty-print JSON
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from codex_window import get_budget  # noqa: E402


def main() -> int:
    force_refresh = "--refresh" in sys.argv
    pretty = "--pretty" in sys.argv

    budget = get_budget(force_refresh=force_refresh)
    if pretty:
        print(json.dumps(budget, indent=2))
    else:
        print(json.dumps(budget))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
