"""Guard: no tracked file has a Windows-illegal path character.

A tracked filename containing a character that is legal on Linux but illegal in
a Windows path (most commonly ``:`` from an NTFS ``Zone.Identifier`` mark-of-web
artifact) checks out fine on Linux but aborts ``git checkout`` on a Windows
runner with exit 128 — *before any workflow step runs*. PR #24's Windows
``install.ps1`` smoke job was red for exactly this reason
(``claude-md/CLAUDE.md:Zone.Identifier``, committed before ``.gitignore`` grew
its ``*:Zone.Identifier`` rule — and a gitignore rule cannot untrack an
already-tracked file).

This guard runs on Linux and fails fast + legibly the moment such a path is
tracked, instead of surfacing as an opaque Windows-only checkout abort. It reads
the git index via ``git ls-files`` — deterministic, no network, no LLM.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Characters illegal in a Windows path component. The path separator "/" is
# excluded (git uses it as the segment delimiter); every other reserved char
# would break a Windows `git checkout`.
_WINDOWS_ILLEGAL = set('<>:"\\|?*')


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def test_no_tracked_file_has_windows_illegal_path():
    bad = [
        path
        for path in _tracked_files()
        if any(ch in _WINDOWS_ILLEGAL for ch in path.replace("/", ""))
    ]
    assert not bad, (
        "Tracked files have Windows-illegal path characters — these abort "
        "`git checkout` on Windows runners (exit 128), breaking the Windows CI "
        "job before any step runs:\n"
        + "\n".join(f"  - {p}" for p in bad)
        + "\nRemove them with `git rm`. (.gitignore already excludes "
        "*:Zone.Identifier, but it cannot untrack a file committed before the rule.)"
    )
