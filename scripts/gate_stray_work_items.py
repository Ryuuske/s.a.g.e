#!/usr/bin/env python3
"""Gate: work items live ONLY in internal/BACKLOG.md.

Master Run Stage 1 standing rule (internal/ledger.md Entry 001/004).
Scans every tracked file (git ls-files) for stray work-item markers and
unchecked checkboxes. Word-bounded, case-sensitive matching per the Stage 1
referee verdict Amendment 1 — incidental substrings (mktemp XXXX templates,
token fixtures like glpat-XXXX..., words containing "TODO") do not match.

Allowlist policy: files whose CONTENT legitimately contains marker shapes
(pattern definitions, reviewer instructions, fixtures, templates). Any
addition to an allowlist requires a new entry in internal/ledger.md.

Exit 0 = clean. Exit 1 = stray work item found (printed as file:line:match).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Case-sensitive, word-bounded. \b keeps XXXX-runs and GLYPTODON out.
MARKER = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b")
CHECKBOX = re.compile(r"^\s*[-*] \[ \]")

# Marker-shaped CONTENT that is not a work item (ledger Entry 004 baseline).
MARKER_ALLOW = {
    "agents/aidev-code-reviewer.md",  # reviewer instruction: flag TODOs in diffs
    "agents/dev-code-reviewer.md",  # reviewer instruction: flag TODOs in diffs
    "agents/ops-release-readiness.md",  # release gate names the markers it hunts
    "claude-md/forbidden-patterns-template.md",  # defines the TODO pattern itself
    "tests/test_normalize.py",  # fixture strings exercise grep normalization
    "scripts/gate_stray_work_items.py",  # this gate's own pattern definitions
}

# Checkbox templates / format examples that are not work items.
CHECKBOX_ALLOW = {
    ".github/PULL_REQUEST_TEMPLATE.md",  # PR checklist by design
    "agents/ops-release-readiness.md",  # verdict-card template placeholders
    "skills/gh-scaffold-discipline/SKILL.md",  # scaffolding checklist template
    "docs/specs/backlog-changelog-schema.md",  # retired-convention format examples
}

# Whole trees excluded until their disposition lands (ledger Entry 001 Q3).
PREFIX_ALLOW = ("docs/projects/sage-estate-dashboard/",)


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


def main() -> int:
    hits: list[str] = []
    for rel in tracked_files():
        if rel.startswith(PREFIX_ALLOW):
            continue
        path = REPO / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue  # binary or vanished — not a prose/code surface
        for lineno, line in enumerate(text.splitlines(), 1):
            if rel not in MARKER_ALLOW:
                m = MARKER.search(line)
                if m:
                    hits.append(f"{rel}:{lineno}: stray marker {m.group(0)!r}")
            if rel not in CHECKBOX_ALLOW and CHECKBOX.search(line):
                hits.append(f"{rel}:{lineno}: stray unchecked checkbox")
    if hits:
        print("Stray work items found — work items live ONLY in internal/BACKLOG.md:")
        print("\n".join(hits))
        print(
            f"\n{len(hits)} hit(s). Either move the item to internal/BACKLOG.md "
            "or (for legitimate marker-shaped content) add the file to the "
            "allowlist in scripts/gate_stray_work_items.py WITH a ledger entry."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
