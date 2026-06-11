"""
test_install_managed_hooks.py — Coverage for install.sh's copy_managed() behavior
and the matching Copy-FileManaged in install.ps1.

Codex PR#24 New-F2, issue #25.

Acceptance criteria tested:
  1. A sage-managed hook file with OLD content + a normal (non-force) reinstall
     → the file is REFRESHED to new (repo) content and a backup is taken.
  2. A user-content file (CLAUDE.md, via copy_file) is NOT refreshed without --force
     — its skip-if-exists logic is unchanged.

Each test drives the real bash install.sh via subprocess with CLAUDE_DIR pointing
at an isolated tmp directory, following the same pattern as test_install_fence_merge.py.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Locate install.sh
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_INSTALL_SH = _REPO_ROOT / "install.sh"

if not _INSTALL_SH.is_file():
    pytest.skip(
        f"install.sh not found at {_INSTALL_SH} — skipping managed-hook tests",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration


# ===========================================================================
# Case 1 — managed hook file with OLD content + normal reinstall → REFRESHED
# ===========================================================================


class TestManagedHookRefreshesOnReinstall:
    """copy_managed() always overwrites sage-owned hook files on reinstall.

    A normal (non-force) reinstall must refresh the installed hook to the
    repo's current content even if the destination already exists.
    """

    # The three managed hook filenames install.sh copies to $HOOKS_DST.
    _MANAGED_HOOKS = [
        "inject-codex-budget.py",
        "autonomy-continuation-sessionstart.py",
        "claude-wakeup-sessionstart.py",
    ]

    def _plant_old_content(self, hooks_dst: Path, filename: str) -> None:
        """Write OLD sentinel content to simulate a stale installed copy."""
        (hooks_dst / filename).write_text(
            "# OLD HOOK CONTENT — should be refreshed on reinstall\n",
            encoding="utf-8",
        )

    def _get_repo_content(self, filename: str) -> str:
        """Return the current content of the hook in installer-assets/."""
        src = _REPO_ROOT / "installer-assets" / filename
        return src.read_text(encoding="utf-8")

    def test_inject_codex_budget_refreshed(self, tmp_path: Path) -> None:
        """inject-codex-budget.py with stale content is updated on normal reinstall."""
        filename = "inject-codex-budget.py"
        claude_dir = tmp_path / "dot_claude"
        claude_dir.mkdir()
        hooks_dst = claude_dir / "hooks"
        hooks_dst.mkdir()
        self._plant_old_content(hooks_dst, filename)

        env = os.environ.copy()
        env["CLAUDE_DIR"] = str(claude_dir)
        env["HOME"] = str(tmp_path)

        subprocess.run(
            ["bash", str(_INSTALL_SH), "--no-per-repo-claude-md"],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env=env,
        )
        # install.sh may exit nonzero if sage-mcp is missing — only the hook
        # file content matters for this test.
        dst_content = (hooks_dst / filename).read_text(encoding="utf-8")
        repo_content = self._get_repo_content(filename)
        assert dst_content == repo_content, (
            f"{filename}: expected repo content after reinstall, "
            f"got stale content (copy_managed must always refresh)"
        )
        assert "OLD HOOK CONTENT" not in dst_content

    def test_autonomy_continuation_refreshed(self, tmp_path: Path) -> None:
        """autonomy-continuation-sessionstart.py is updated on normal reinstall."""
        filename = "autonomy-continuation-sessionstart.py"
        claude_dir = tmp_path / "dot_claude"
        claude_dir.mkdir()
        hooks_dst = claude_dir / "hooks"
        hooks_dst.mkdir()
        self._plant_old_content(hooks_dst, filename)

        env = os.environ.copy()
        env["CLAUDE_DIR"] = str(claude_dir)
        env["HOME"] = str(tmp_path)

        subprocess.run(
            ["bash", str(_INSTALL_SH), "--no-per-repo-claude-md"],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env=env,
        )
        dst_content = (hooks_dst / filename).read_text(encoding="utf-8")
        repo_content = self._get_repo_content(filename)
        assert dst_content == repo_content, (
            f"{filename}: stale content must be refreshed on normal reinstall"
        )

    def test_wakeup_sessionstart_refreshed(self, tmp_path: Path) -> None:
        """claude-wakeup-sessionstart.py is updated on normal reinstall."""
        filename = "claude-wakeup-sessionstart.py"
        claude_dir = tmp_path / "dot_claude"
        claude_dir.mkdir()
        hooks_dst = claude_dir / "hooks"
        hooks_dst.mkdir()
        self._plant_old_content(hooks_dst, filename)

        env = os.environ.copy()
        env["CLAUDE_DIR"] = str(claude_dir)
        env["HOME"] = str(tmp_path)

        subprocess.run(
            ["bash", str(_INSTALL_SH), "--no-per-repo-claude-md"],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env=env,
        )
        dst_content = (hooks_dst / filename).read_text(encoding="utf-8")
        repo_content = self._get_repo_content(filename)
        assert dst_content == repo_content, (
            f"{filename}: stale content must be refreshed on normal reinstall"
        )

    def test_refresh_logs_refresh_message(self, tmp_path: Path) -> None:
        """install.sh logs a 'refresh (managed)' message when overwriting a hook."""
        filename = "inject-codex-budget.py"
        claude_dir = tmp_path / "dot_claude"
        claude_dir.mkdir()
        hooks_dst = claude_dir / "hooks"
        hooks_dst.mkdir()
        self._plant_old_content(hooks_dst, filename)

        env = os.environ.copy()
        env["CLAUDE_DIR"] = str(claude_dir)
        env["HOME"] = str(tmp_path)

        result = subprocess.run(
            ["bash", str(_INSTALL_SH), "--no-per-repo-claude-md"],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env=env,
        )
        combined = result.stdout + result.stderr
        assert "refresh (managed)" in combined, (
            f"expected 'refresh (managed)' log line in output; got:\n{combined}"
        )

    def test_backup_taken_before_overwrite(self, tmp_path: Path) -> None:
        """copy_managed takes a backup of the existing hook before overwriting."""
        filename = "inject-codex-budget.py"
        old_sentinel = "# OLD HOOK CONTENT — backup me\n"
        claude_dir = tmp_path / "dot_claude"
        claude_dir.mkdir()
        hooks_dst = claude_dir / "hooks"
        hooks_dst.mkdir()
        (hooks_dst / filename).write_text(old_sentinel, encoding="utf-8")

        env = os.environ.copy()
        env["CLAUDE_DIR"] = str(claude_dir)
        env["HOME"] = str(tmp_path)

        subprocess.run(
            ["bash", str(_INSTALL_SH), "--no-per-repo-claude-md"],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env=env,
        )

        # The backup dir is timestamped under CLAUDE_DIR/backup/.
        # Find any backup copy of the hook file.
        backup_files = list((claude_dir / "backup").rglob(filename))
        assert backup_files, (
            f"expected a backup of {filename} under {claude_dir}/backup/; none found"
        )
        backup_content = backup_files[0].read_text(encoding="utf-8")
        assert backup_content == old_sentinel, (
            f"backup must contain the OLD content before overwrite; got: {backup_content!r}"
        )


# ===========================================================================
# Case 2 — dry-run: managed hook simulation, no writes
# ===========================================================================


class TestManagedHookDryRun:
    """--dry-run must simulate refresh without writing."""

    def test_dry_run_logs_would_refresh(self, tmp_path: Path) -> None:
        """--dry-run emits 'would refresh (managed)' without changing the file."""
        filename = "inject-codex-budget.py"
        old_sentinel = "# OLD HOOK — must survive dry-run\n"
        claude_dir = tmp_path / "dot_claude"
        claude_dir.mkdir()
        hooks_dst = claude_dir / "hooks"
        hooks_dst.mkdir()
        (hooks_dst / filename).write_text(old_sentinel, encoding="utf-8")

        env = os.environ.copy()
        env["CLAUDE_DIR"] = str(claude_dir)
        env["HOME"] = str(tmp_path)

        result = subprocess.run(
            ["bash", str(_INSTALL_SH), "--dry-run", "--no-per-repo-claude-md"],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env=env,
        )
        combined = result.stdout + result.stderr
        assert "would refresh (managed)" in combined, (
            f"dry-run must emit 'would refresh (managed)' log; got:\n{combined}"
        )
        # File must be UNCHANGED under dry-run — the real no-write guarantee.
        assert (hooks_dst / filename).read_text(encoding="utf-8") == old_sentinel, (
            "--dry-run must NOT modify the hook file"
        )


# ===========================================================================
# Case 3 — user-content file (CLAUDE.md) is NOT force-overwritten by managed path
# ===========================================================================


class TestUserContentNotManagedOverwritten:
    """CLAUDE.md uses copy_file (skip-if-exists), not copy_managed.

    A normal (non-force) reinstall must NOT overwrite an existing CLAUDE.md.
    This verifies that the managed path was not accidentally applied to
    user-touchable files.
    """

    _PERSONAL_CONTENT = (
        "# My personal CLAUDE.md\n"
        "This is user-written content that must survive a reinstall.\n"
        "<!-- BEGIN SAGE -->\n"
        "# Old sage spine\n"
        "<!-- END SAGE -->\n"
    )

    def test_claude_md_not_overwritten_without_force(self, tmp_path: Path) -> None:
        """Normal reinstall must leave an existing CLAUDE.md with no SAGE markers
        unchanged (copy_file skip-if-exists path stays on user content).

        Note: if the CLAUDE.md already has SAGE markers, install.sh will
        fence-merge the spine block — that is still correct behavior and does
        NOT count as 'force-overwriting user content'.  We use a fenceless
        CLAUDE.md here to hit the copy_file skip path.
        """
        fenceless_content = "# My personal CLAUDE.md — no sage markers\nUser content.\n"

        claude_dir = tmp_path / "dot_claude"
        claude_dir.mkdir()
        claude_md_dst = claude_dir / "CLAUDE.md"
        claude_md_dst.write_text(fenceless_content, encoding="utf-8")

        env = os.environ.copy()
        env["CLAUDE_DIR"] = str(claude_dir)
        env["HOME"] = str(tmp_path)

        subprocess.run(
            ["bash", str(_INSTALL_SH), "--no-per-repo-claude-md", "--claude-md=no"],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env=env,
        )
        after = claude_md_dst.read_text(encoding="utf-8")
        assert after == fenceless_content, (
            "CLAUDE.md (user-content file) must NOT be overwritten on normal reinstall "
            "— only sage-managed hook files use copy_managed"
        )


# ===========================================================================
# Case 4 — symlinked dst is replaced, not followed (Codex PR#26 P2)
# ===========================================================================


class TestCopyManagedReplacesSymlink:
    """copy_managed() must REPLACE a symlinked dst, not follow it.

    If $dst is a symlink into a dotfiles-managed setup (or any file outside
    ~/.claude/hooks), plain cp would dereference the symlink and overwrite the
    TARGET, clobbering files outside the hooks dir.  The fix: rm -f $dst before
    cp so the symlink itself is replaced with a regular file.

    Assertions (Codex PR#26 acceptance criteria):
      (a) dst is now a regular file with the repo hook content (symlink replaced)
      (b) the SENTINEL target file outside hooks/ is UNCHANGED (not clobbered)
      (c) a backup was taken before the rm+cp
    """

    def test_symlinked_dst_replaced_not_followed(self, tmp_path: Path) -> None:
        """copy_managed replaces a symlinked hook dst; the symlink target is untouched."""
        filename = "inject-codex-budget.py"

        # Set up isolated install tree.
        claude_dir = tmp_path / "dot_claude"
        claude_dir.mkdir()
        hooks_dst = claude_dir / "hooks"
        hooks_dst.mkdir()

        # SENTINEL: a file OUTSIDE ~/.claude/hooks simulating a dotfiles-managed location.
        sentinel_dir = tmp_path / "dotfiles"
        sentinel_dir.mkdir()
        sentinel_file = sentinel_dir / filename
        sentinel_content = "# SENTINEL — must NOT be clobbered by copy_managed\n"
        sentinel_file.write_text(sentinel_content, encoding="utf-8")

        # Plant a symlink at the hook destination pointing at the sentinel.
        hook_link = hooks_dst / filename
        hook_link.symlink_to(sentinel_file)
        assert hook_link.is_symlink(), "pre-condition: dst must be a symlink"
        assert hook_link.is_file(), "pre-condition: symlink must resolve"

        env = os.environ.copy()
        env["CLAUDE_DIR"] = str(claude_dir)
        env["HOME"] = str(tmp_path)

        subprocess.run(
            ["bash", str(_INSTALL_SH), "--no-per-repo-claude-md"],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env=env,
        )

        # (a) dst is now a REGULAR file with the new hook content — symlink replaced.
        assert not hook_link.is_symlink(), (
            "copy_managed must replace the symlink with a regular file; "
            "dst is still a symlink after install"
        )
        assert hook_link.is_file(), "dst must be a regular file after copy_managed"
        repo_content = (_REPO_ROOT / "installer-assets" / filename).read_text(encoding="utf-8")
        dst_content = hook_link.read_text(encoding="utf-8")
        assert dst_content == repo_content, (
            "copy_managed must write the repo hook content to dst after replacing the symlink"
        )

        # (b) The SENTINEL target outside hooks/ is UNCHANGED.
        assert sentinel_file.read_text(encoding="utf-8") == sentinel_content, (
            "copy_managed must NOT clobber the symlink target outside ~/.claude/hooks; "
            "sentinel file was modified"
        )

        # (c) A backup was taken before removal.
        backup_files = list((claude_dir / "backup").rglob(filename))
        assert backup_files, (
            f"expected a backup of {filename} under {claude_dir}/backup/ before symlink removal; "
            "none found"
        )


# ===========================================================================
# Case 5 — source-ordering invariant: all 3 managed SessionStart hooks are
#           copy_managed'd BEFORE do_git_commit fires (Codex PR#26 P2 fix)
# ===========================================================================


class TestManagedHooksCopiedBeforeGitCommit:
    """Source-ordering assertion: all three managed SessionStart hook copies
    precede the do_git_commit invocation in install.sh.

    On a git-backed ~/.claude install, do_git_commit stages hooks/ and commits.
    Any hook copied AFTER that call is left unstaged/uncommitted — a silent
    regression where the git history does not capture the refresh.

    This test parses install.sh and asserts the ordering invariant:
      line(copy_managed inject-codex-budget.py)       < line(do_git_commit invocation)
      line(copy_managed autonomy-continuation-…py)    < line(do_git_commit invocation)
      line(copy_managed claude-wakeup-sessionstart.py) < line(do_git_commit invocation)

    A source-ordering test is appropriate here because the ordering IS the fix —
    runtime behavior in a git-backed ~/.claude is hard to exercise in CI (requires
    a real git repo at CLAUDE_DIR), so the structural assertion is the contract.
    """

    _HOOKS = [
        "inject-codex-budget.py",
        "autonomy-continuation-sessionstart.py",
        "claude-wakeup-sessionstart.py",
    ]

    def _find_first_copy_managed_line(self, lines: list[str], hook_filename: str) -> int:
        """Return the 1-based line number of the first copy_managed call for hook_filename."""
        for i, line in enumerate(lines, start=1):
            if "copy_managed" in line and hook_filename in line:
                return i
        return -1

    def _find_do_git_commit_invocation_line(self, lines: list[str]) -> int:
        """Return the 1-based line number of the do_git_commit invocation (not definition).

        The function DEFINITION line starts with 'do_git_commit()'; the INVOCATION
        line calls 'do_git_commit "$CLAUDE_DIR"' (with an argument).
        """
        for i, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("do_git_commit ") and "CLAUDE_DIR" in stripped:
                return i
        return -1

    def test_all_three_hooks_copied_before_git_commit(self) -> None:
        """All 3 managed SessionStart hook copy_managed calls precede do_git_commit."""
        install_sh = _REPO_ROOT / "install.sh"
        lines = install_sh.read_text(encoding="utf-8").splitlines()

        commit_line = self._find_do_git_commit_invocation_line(lines)
        assert commit_line > 0, (
            "Could not find do_git_commit invocation in install.sh — "
            "is the git-commit section missing?"
        )

        for hook in self._HOOKS:
            copy_line = self._find_first_copy_managed_line(lines, hook)
            assert copy_line > 0, f"Could not find copy_managed call for {hook!r} in install.sh"
            assert copy_line < commit_line, (
                f"ORDERING REGRESSION: copy_managed for {hook!r} appears at line {copy_line}, "
                f"but do_git_commit invocation is at line {commit_line}. "
                f"The hook refresh must precede the git auto-commit so git-backed ~/.claude "
                f"installs capture all three managed SessionStart hooks in the same commit. "
                f"(Codex PR#26 P2 fix)"
            )


# ===========================================================================
# Case 6 — idempotent reinstall: identical dst → no backup, no churn
#           (Codex PR#26 root-cause fix)
# ===========================================================================


class TestCopyManagedIdempotentWhenIdentical:
    """copy_managed() skips backup+overwrite when dst is already byte-identical.

    Root cause of the Codex PR#26 abort chain: copy_managed's unconditional
    backup+overwrite created untracked backup/ files even on a no-op reinstall,
    making git status dirty with nothing staged → git commit failed → installer
    aborted under set -e.

    The fix: when dst exists, is a regular file (not a symlink), and is
    byte-identical to src (cmp -s), skip entirely — log 'up-to-date (managed)'.

    Acceptance criteria:
      (a) Idempotent reinstall → no backup/ directory created, dst unchanged.
      (b) Changed content → backup+refresh still happens (the normal upgrade path).
      (c) Symlinked dst → still replaced (prior fix intact — not treated as identical).
    """

    def _get_repo_content(self, filename: str) -> bytes:
        """Return the real bytes of the hook in installer-assets/."""
        src = _REPO_ROOT / "installer-assets" / filename
        return src.read_bytes()

    def test_idempotent_reinstall_no_backup_no_churn(self, tmp_path: Path) -> None:
        """copy_managed does NOT create a backup when dst is already identical to src."""
        filename = "inject-codex-budget.py"
        repo_bytes = self._get_repo_content(filename)

        claude_dir = tmp_path / "dot_claude"
        claude_dir.mkdir()
        hooks_dst = claude_dir / "hooks"
        hooks_dst.mkdir()

        # Plant dst with IDENTICAL content to the repo source.
        dst_file = hooks_dst / filename
        dst_file.write_bytes(repo_bytes)

        env = os.environ.copy()
        env["CLAUDE_DIR"] = str(claude_dir)
        env["HOME"] = str(tmp_path)

        result = subprocess.run(
            ["bash", str(_INSTALL_SH), "--no-per-repo-claude-md"],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env=env,
        )
        combined = result.stdout + result.stderr

        # (a) No backup directory should be created.
        backup_dir = claude_dir / "backup"
        assert not backup_dir.exists() or not list(backup_dir.rglob(filename)), (
            "copy_managed must NOT create a backup when dst is already byte-identical to src; "
            f"backup file found under {backup_dir}. Output:\n{combined}"
        )

        # (b) dst content must be unchanged.
        assert dst_file.read_bytes() == repo_bytes, (
            "copy_managed must NOT modify dst when content is already identical"
        )

        # (c) Log line must say 'up-to-date (managed)'.
        assert "up-to-date (managed)" in combined, (
            f"copy_managed must log 'up-to-date (managed)' on idempotent reinstall; "
            f"got:\n{combined}"
        )

    def test_changed_content_still_refreshed(self, tmp_path: Path) -> None:
        """copy_managed still backup+overwrites when dst content differs from src."""
        filename = "inject-codex-budget.py"
        repo_content = (_REPO_ROOT / "installer-assets" / filename).read_text(encoding="utf-8")
        old_sentinel = "# STALE CONTENT — different from repo\n"

        claude_dir = tmp_path / "dot_claude"
        claude_dir.mkdir()
        hooks_dst = claude_dir / "hooks"
        hooks_dst.mkdir()
        dst_file = hooks_dst / filename
        dst_file.write_text(old_sentinel, encoding="utf-8")

        env = os.environ.copy()
        env["CLAUDE_DIR"] = str(claude_dir)
        env["HOME"] = str(tmp_path)

        subprocess.run(
            ["bash", str(_INSTALL_SH), "--no-per-repo-claude-md"],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env=env,
        )

        # Content must be refreshed to repo version.
        assert dst_file.read_text(encoding="utf-8") == repo_content, (
            "copy_managed must refresh dst when content differs from src"
        )
        # Backup must exist with old content.
        backup_files = list((claude_dir / "backup").rglob(filename))
        assert backup_files, (
            f"copy_managed must take a backup when content differs; "
            f"no backup found under {claude_dir}/backup/"
        )
        assert backup_files[0].read_text(encoding="utf-8") == old_sentinel

    def test_symlinked_dst_still_replaced_not_treated_as_identical(self, tmp_path: Path) -> None:
        """copy_managed replaces symlinked dst even if symlink target matches src content.

        A symlink must NEVER be treated as identical — even if the target file's
        bytes happen to match — because the dst entry is still a symlink that
        must be converted to a regular file.
        """
        filename = "inject-codex-budget.py"
        repo_content = (_REPO_ROOT / "installer-assets" / filename).read_text(encoding="utf-8")

        claude_dir = tmp_path / "dot_claude"
        claude_dir.mkdir()
        hooks_dst = claude_dir / "hooks"
        hooks_dst.mkdir()

        # Create a file OUTSIDE hooks/ with IDENTICAL content to the repo source.
        # A cmp -s on the symlink would see identical bytes — copy_managed must
        # still replace the symlink (because dst is a symlink, not a regular file).
        outside_dir = tmp_path / "dotfiles"
        outside_dir.mkdir()
        outside_file = outside_dir / filename
        outside_file.write_text(repo_content, encoding="utf-8")

        hook_link = hooks_dst / filename
        hook_link.symlink_to(outside_file)
        assert hook_link.is_symlink(), "pre-condition: dst must be a symlink"

        env = os.environ.copy()
        env["CLAUDE_DIR"] = str(claude_dir)
        env["HOME"] = str(tmp_path)

        subprocess.run(
            ["bash", str(_INSTALL_SH), "--no-per-repo-claude-md"],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env=env,
        )

        # dst must be a regular file (symlink replaced), not still a symlink.
        assert not hook_link.is_symlink(), (
            "copy_managed must replace a symlinked dst with a regular file, "
            "even when the symlink target's content is identical to src"
        )
        assert hook_link.is_file(), "dst must be a regular file after copy_managed"


# ===========================================================================
# Case 7 — do_git_commit is empty-safe: no abort on idempotent reinstall
#           (Codex PR#26 defense-in-depth fix)
# ===========================================================================


class TestDoGitCommitEmptySafe:
    """do_git_commit does not abort on a git-backed CLAUDE_DIR reinstall.

    Defense-in-depth fix: do_git_commit stages only paths that exist and only
    commits when there is something staged.  This prevents two related failures:

    1. `git add CLAUDE.md` aborting when CLAUDE.md was skipped (--claude-md=no) —
       `fatal: pathspec ... did not match any files` propagated via set -e.
    2. `git commit` running with nothing staged when all content was already
       identical — `git commit` exits nonzero and aborts the installer.

    This test drives install.sh against a real git-backed CLAUDE_DIR where
    the managed hook files are already byte-identical to the repo sources.
    The installer must complete (exit 0 or only fail for sage-mcp/plugin reasons),
    and the hooks must be logged as 'up-to-date' with no backup created.
    """

    def _git(self, *args: str, cwd: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=str(cwd),
        )

    def test_identical_reinstall_does_not_abort_on_git_backed_claude_dir(
        self, tmp_path: Path
    ) -> None:
        """installer exits 0 on idempotent reinstall into a git-backed CLAUDE_DIR."""
        claude_dir = tmp_path / "dot_claude"
        claude_dir.mkdir()
        hooks_dst = claude_dir / "hooks"
        hooks_dst.mkdir()

        # Initialise a bare git repo at CLAUDE_DIR.
        self._git("init", cwd=claude_dir)
        self._git("config", "user.email", "test@test.com", cwd=claude_dir)
        self._git("config", "user.name", "Test", cwd=claude_dir)
        # Create an initial commit so HEAD exists (git diff --cached needs it).
        (claude_dir / ".gitkeep").write_text("", encoding="utf-8")
        self._git("add", ".gitkeep", cwd=claude_dir)
        self._git("commit", "-m", "init", cwd=claude_dir)

        # Plant each managed hook with IDENTICAL content to the repo source.
        for filename in [
            "inject-codex-budget.py",
            "autonomy-continuation-sessionstart.py",
            "claude-wakeup-sessionstart.py",
        ]:
            src = _REPO_ROOT / "installer-assets" / filename
            (hooks_dst / filename).write_bytes(src.read_bytes())

        # Commit the hooks so git sees them as tracked (not new).
        self._git("add", "hooks/", cwd=claude_dir)
        self._git("commit", "-m", "pre-seed hooks", cwd=claude_dir)

        env = os.environ.copy()
        env["CLAUDE_DIR"] = str(claude_dir)
        env["HOME"] = str(tmp_path)
        # Suppress interactive CLAUDE.md prompt.
        env["TERM"] = ""

        result = subprocess.run(
            ["bash", str(_INSTALL_SH), "--no-per-repo-claude-md", "--claude-md=no"],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env=env,
        )
        combined = result.stdout + result.stderr

        # The installer must NOT abort (exit nonzero) due to an empty git commit.
        # We accept nonzero ONLY if it's due to sage-mcp/plugin not being on PATH
        # (expected in CI) — detect that sentinel and allow it.
        sage_mcp_absent = (
            "sage-mcp NOT on PATH" in combined
            or "no 'claude' CLI" in combined
            or "claude plugin install" in combined
        )
        if result.returncode != 0 and not sage_mcp_absent:
            pytest.fail(
                f"installer aborted (exit {result.returncode}) on reinstall "
                f"into git-backed CLAUDE_DIR.\n"
                f"Output:\n{combined}"
            )

        # The hooks must have been identified as up-to-date (not refreshed/backed-up).
        # This is the core PR#26 idempotency assertion: copy_managed skipped the hooks
        # because they were already byte-identical.
        for hook_name in [
            "inject-codex-budget.py",
            "autonomy-continuation-sessionstart.py",
            "claude-wakeup-sessionstart.py",
        ]:
            assert "up-to-date (managed): " in combined and hook_name in combined, (
                f"expected 'up-to-date (managed)' for {hook_name} in installer output; "
                f"got:\n{combined}"
            )
            # No backup must be created for the identical hooks.
            backup_dir = claude_dir / "backup"
            hook_backups = list(backup_dir.rglob(hook_name)) if backup_dir.exists() else []
            assert not hook_backups, (
                f"copy_managed must NOT create a backup for {hook_name} when dst is "
                f"already byte-identical to src; backup found: {hook_backups}"
            )
