#!/usr/bin/env python3
"""Doc gates: link integrity, shipped-cites-shipped, countable facts, banned
terms, registry reachability, CLAUDE.md line budget.

Master Run Stage 3e (C-03: drift control = generation + gates; Delta 3:
SHIPPED DOCS MAY ONLY CITE SHIPPED DOCS). Pure stdlib; CI-safe.

Checks (any hit = exit 1):
  1. Every relative markdown link in a tracked .md resolves to a tracked file.
  2. Links FROM shipped docs (per src/sage_mcp/export.py allowlist) land only on
     shipped targets.
  3. Countable facts (N agents/skills/commands) banned in hand-written prose
     docs — counts live ONLY in generated docs/reference/surface.md.
  4. Banned terms: the pre-rename project name outside the historical allowlist.
  5. Every tracked docs/**.md is reachable from docs/index.md (path mention or
     covered directory prefix).
  6. Root CLAUDE.md stays within its 50-line budget.

Allowlist additions require an .development/ledger.md entry (Entry 004 policy).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
from sage_mcp.export import SHIP_DIRS, SHIP_FILES  # noqa: E402

# -- check 1/2: links ---------------------------------------------------------
LINK = re.compile(r"\]\(([^)\s]+)\)")

# -- check 3: countable facts -------------------------------------------------
COUNT = re.compile(r"\b\d+\s+(?:repo\s+)?(agents|skills|commands)\b", re.IGNORECASE)
COUNT_PROSE_PREFIXES = ("docs/",)
COUNT_PROSE_FILES = {
    "README.md",
    "CLAUDE.md",
    "statusline/README.md",
    "installer-assets/README-claude-wakeup.md",
    ".claude-plugin/README.md",
}
COUNT_ALLOW = {
    # generated — counts are the point
    "docs/reference/agent-roster.md",
    "docs/reference/skills.md",
    "docs/reference/commands.md",
    "docs/reference/surface.md",
    # historical-fenced baseline snapshots (charter §7/§8 2026-05-28 baseline)
    "docs/specs/framework-standards-charter.md",
    # fixed convention scope ("the 9 aidev-* agents", ADR-0010 C2), not a drifting count
    ".development/agents/README.md",
    "docs/specs/backlog-changelog-schema.md",
}

# -- check 4: banned terms ----------------------------------------------------
BANNED = re.compile(
    r"solo[-_]palace|\b" + "".join(["ke", "el"]) + r"\b", re.IGNORECASE
)  # second alt = the pre-rebrand name, assembled to self-pass
BANNED_ALLOW = {
    "CHANGELOG.md",  # append-only release history (reader-note fenced)
    "scripts/migrate_to_sage.py",  # migrates the pre-rebrand store (documents its path)
    "scripts/gate_docs.py",  # this file's allowlist
    ".development/BACKLOG.md",  # dev-only work items
}
BANNED_ALLOW_PREFIXES = ("tests/",)  # migration-coverage fixtures

# -- check 4b: PyPI install commands for the unresolved name (B-031) -----------
# Distribution is GitHub-only (decision record 0097); the PyPI names sage/
# sage-mcp belong to strangers, so `... install sage` is banned everywhere
# except warning lines that quote the command to warn against it.
PYPI_CMD = re.compile(r"(uv tool |pip3? |python3? -m pip |-m pip )install sage\b")
PYPI_CMD_ALLOW_FILES = {
    "README.md",
    ".claude-plugin/README.md",
    "docs/guides/onboarding.md",
    "docs/guides/releasing.md",
    "scripts/gate_docs.py",
}
_PYPI_WARN_WORDS = ("taken", "unrelated", "not on PyPI", "NOT on PyPI", "stranger")

# -- check 5: reachability ----------------------------------------------------
REACH_PREFIX_COVERED = (
    ".development/agents/",  # covered by its registry row
    "docs/reference/",  # generated; registry row covers
)

CLAUDE_MD_BUDGET = 50


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


def shipped(paths: list[str]) -> set[str]:
    s = set()
    for rel in paths:
        if rel in SHIP_FILES or any(rel == d or rel.startswith(d + "/") for d in SHIP_DIRS):
            s.add(rel)
    return s


def _banned_scans(files: list[str]) -> list[str]:
    """Checks 4 + 4b: banned terms and unresolved-name PyPI install commands."""
    errors: list[str] = []
    for rel in files:
        try:
            text = (REPO / rel).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        # 4: banned terms
        if rel not in BANNED_ALLOW and not any(rel.startswith(p) for p in BANNED_ALLOW_PREFIXES):
            m = BANNED.search(text)
            if m:
                errors.append(f"{rel}: banned term {m.group(0)!r}")
        # 4b: unresolved-name PyPI install commands (B-031 class gate)
        for lineno, line in enumerate(text.splitlines(), 1):
            if PYPI_CMD.search(line):
                if rel in PYPI_CMD_ALLOW_FILES and any(w in line for w in _PYPI_WARN_WORDS):
                    continue  # quoted-to-warn, in a declared warning file
                errors.append(
                    f"{rel}:{lineno}: PyPI install command for the unresolved name (B-031)"
                )
    return errors


def main() -> int:
    files = tracked()
    tracked_set = set(files)
    ship_set = shipped(files)
    errors: list[str] = []

    index_text = (REPO / "docs/index.md").read_text(encoding="utf-8")

    for rel in files:
        if not rel.endswith(".md"):
            continue
        text = (REPO / rel).read_text(encoding="utf-8")
        linkable = re.sub(r"```.*?```", "", text, flags=re.S)  # template/code fences are not links

        # 1+2: relative links
        for m in LINK.finditer(linkable):
            target = m.group(1)
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target_path = target.split("#")[0]
            if not target_path:
                continue
            resolved = (Path(rel).parent / target_path).as_posix()
            parts: list[str] = []
            for seg in resolved.split("/"):
                if seg == "..":
                    if parts:
                        parts.pop()
                elif seg not in (".", ""):
                    parts.append(seg)
            norm = "/".join(parts)
            if norm not in tracked_set:
                errors.append(f"{rel}: dead link → {target}")
            elif rel in ship_set and norm not in ship_set:
                errors.append(f"{rel}: SHIPPED doc links non-shipped target → {target} (Delta 3)")

        # 3: countable facts in hand prose
        is_prose = rel in COUNT_PROSE_FILES or any(rel.startswith(p) for p in COUNT_PROSE_PREFIXES)
        if is_prose and rel not in COUNT_ALLOW:
            for m in COUNT.finditer(text):
                errors.append(
                    f"{rel}: countable fact in hand doc: {m.group(0)!r} (home: docs/reference/surface.md)"
                )

        # 5: reachability from the registry
        if rel.startswith("docs/") and rel != "docs/index.md":
            rel_in_docs = rel[len("docs/") :]
            if (
                not any(rel.startswith(p) for p in REACH_PREFIX_COVERED)
                and rel not in index_text
                and rel_in_docs not in index_text
            ):
                errors.append(f"{rel}: not reachable from docs/index.md registry")

    errors.extend(_banned_scans(files))

    # 6: stub budget
    n_lines = len((REPO / "CLAUDE.md").read_text(encoding="utf-8").splitlines())
    if n_lines > CLAUDE_MD_BUDGET:
        errors.append(f"CLAUDE.md: {n_lines} lines exceeds the {CLAUDE_MD_BUDGET}-line budget")

    if errors:
        print(f"doc gates: {len(errors)} finding(s)")
        print("\n".join(errors[:80]))
        return 1
    print("doc gates: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
