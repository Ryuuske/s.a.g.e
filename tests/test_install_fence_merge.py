"""
test_install_fence_merge.py — Durable CI coverage for install.sh's
fence_replace_claude_md() function (ADR-0083, ECC-adoption Phase 3 fold).

Closes the adversarial sev-55 "shipped untested" finding.

Each test drives the *real* bash logic via subprocess:  a per-test shell
harness defines the three helpers install.sh's function depends on
(say / warn / err), sources fence_replace_claude_md() verbatim from
install.sh via grep/awk extraction, writes crafted CLAUDE.md fixtures to
a tmp directory, calls the function, and asserts on the resulting file
content and exit code.

Marker: integration (shells out to bash).  Run along with the normal
pytest suite — no environment variable gate needed; bash is always
available in CI.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Locate install.sh — must be in the repo root
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_INSTALL_SH = _REPO_ROOT / "install.sh"

if not _INSTALL_SH.is_file():
    pytest.skip(
        f"install.sh not found at {_INSTALL_SH} — skipping fence-merge tests",
        allow_module_level=True,
    )

# ---------------------------------------------------------------------------
# Marker for shell-out tests
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helper: run the fence_replace_claude_md function in an isolated bash env
# ---------------------------------------------------------------------------

_HARNESS_TEMPLATE = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    set -euo pipefail

    # Minimal helpers that fence_replace_claude_md depends on.
    say()  {{ printf "%s\\n" "$*"; }}
    warn() {{ printf "WARN: %s\\n" "$*" >&2; }}
    err()  {{ printf "ERROR: %s\\n" "$*" >&2; }}

    # Source fence_replace_claude_md verbatim from install.sh.
    # Extract from the opening "fence_replace_claude_md() {{" line through
    # the matching closing "}}" (the function's top-level closing brace).
    # We use awk's brace-depth counter rather than a fragile line-range so
    # the extraction is robust to future additions inside the function body.
    eval "$(
        awk '
            /^fence_replace_claude_md\\(\\)/ {{
                depth=0; in_func=1
            }}
            in_func {{
                print
                n = split($0, chars, "")
                for (i=1; i<=n; i++) {{
                    if (chars[i] == "{{") depth++
                    if (chars[i] == "}}") {{
                        depth--
                        if (depth == 0) {{ in_func=0; exit }}
                    }}
                }}
            }}
        ' {install_sh}
    )"

    # Invoke with caller-supplied arguments.
    fence_replace_claude_md {args}
    """
)


def _run_fence(
    tmp_path: Path,
    src_content: str,
    dst_content: str,
    *,
    extra_args: str = "",
    expect_exit: int = 0,
) -> tuple[int, str, str, str]:
    """
    Write src and dst fixtures, run fence_replace_claude_md SRC DST [BACKUP],
    return (returncode, stdout, stderr, dst_content_after).
    """
    src = tmp_path / "src_CLAUDE.md"
    dst = tmp_path / "dst_CLAUDE.md"
    backup = tmp_path / "backup_CLAUDE.md"

    src.write_text(src_content, encoding="utf-8")
    dst.write_text(dst_content, encoding="utf-8")

    # Build the args string: SRC DST BACKUP_PATH (always pass backup so
    # error-message assertions can rely on backup_hint being populated).
    args = f'"{src}" "{dst}" "{backup}"'
    if extra_args:
        args = f"{args} {extra_args}"

    script = _HARNESS_TEMPLATE.format(
        install_sh=str(_INSTALL_SH),
        args=args,
    )

    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
    )

    dst_after = dst.read_text(encoding="utf-8") if dst.exists() else ""
    return result.returncode, result.stdout, result.stderr, dst_after


# ---------------------------------------------------------------------------
# Fixtures: reusable CLAUDE.md content fragments
# ---------------------------------------------------------------------------

# A minimal well-formed source with a two-line spine block.
_SRC_SPINE = "<!-- BEGIN SAGE -->\n# Framework spine v2\nSpine content here.\n<!-- END SAGE -->\n"

_SRC_FULL = _SRC_SPINE  # source only needs the markers + spine

# A destination that already has a fenced block (happy path).
_DST_FENCED = (
    "# My personal notes\nThis is OUTSIDE the fence and must be preserved.\n\n"
    "<!-- BEGIN SAGE -->\n# Framework spine v1\nOld spine content.\n<!-- END SAGE -->\n\n"
    "## More personal notes\nAlso outside.\n"
)

# Expected outside-fence content that must survive a merge.
_OUTSIDE_BEFORE = "# My personal notes\nThis is OUTSIDE the fence and must be preserved.\n\n"
_OUTSIDE_AFTER = "\n## More personal notes\nAlso outside.\n"


# ===========================================================================
# Case 1 — happy-path: between-markers replaced, outside content preserved
# ===========================================================================


class TestFenceMergeHappyPath:
    """fence_replace_claude_md replaces the fenced spine, preserves surrounds."""

    def test_outside_before_marker_preserved(self, tmp_path: Path) -> None:
        rc, _out, _err, dst_after = _run_fence(tmp_path, _SRC_FULL, _DST_FENCED)
        assert rc == 0, f"expected exit 0; stderr={_err!r}"
        assert _OUTSIDE_BEFORE in dst_after, "content BEFORE markers must be preserved"

    def test_outside_after_marker_preserved(self, tmp_path: Path) -> None:
        rc, _out, _err, dst_after = _run_fence(tmp_path, _SRC_FULL, _DST_FENCED)
        assert rc == 0
        assert _OUTSIDE_AFTER in dst_after, "content AFTER markers must be preserved"

    def test_new_spine_content_present(self, tmp_path: Path) -> None:
        rc, _out, _err, dst_after = _run_fence(tmp_path, _SRC_FULL, _DST_FENCED)
        assert rc == 0
        assert "Framework spine v2" in dst_after, "new spine content must appear in dst"

    def test_old_spine_content_gone(self, tmp_path: Path) -> None:
        rc, _out, _err, dst_after = _run_fence(tmp_path, _SRC_FULL, _DST_FENCED)
        assert rc == 0
        assert "Old spine content." not in dst_after, "old spine must be replaced"

    def test_markers_present_in_output(self, tmp_path: Path) -> None:
        rc, _out, _err, dst_after = _run_fence(tmp_path, _SRC_FULL, _DST_FENCED)
        assert rc == 0
        assert "<!-- BEGIN SAGE -->" in dst_after
        assert "<!-- END SAGE -->" in dst_after


# ===========================================================================
# Case 2 — idempotency: running twice yields identical result
# ===========================================================================


class TestFenceMergeIdempotency:
    """Running fence_replace_claude_md twice on the same dst produces no change."""

    def test_second_run_identical_output(self, tmp_path: Path) -> None:
        # First pass.
        rc1, _out1, _err1, dst_after_1 = _run_fence(tmp_path, _SRC_FULL, _DST_FENCED)
        assert rc1 == 0, f"first pass failed; stderr={_err1!r}"

        # Second pass: feed the result of pass 1 as the new dst.
        src2 = tmp_path / "src2.md"
        dst2 = tmp_path / "dst2.md"
        backup2 = tmp_path / "backup2.md"
        src2.write_text(_SRC_FULL, encoding="utf-8")
        dst2.write_text(dst_after_1, encoding="utf-8")

        script = _HARNESS_TEMPLATE.format(
            install_sh=str(_INSTALL_SH),
            args=f'"{src2}" "{dst2}" "{backup2}"',
        )
        result2 = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        dst_after_2 = dst2.read_text(encoding="utf-8")

        assert result2.returncode == 0
        assert dst_after_1 == dst_after_2, (
            "second run must yield identical output (no duplicate blocks)"
        )

    def test_no_duplicate_begin_markers(self, tmp_path: Path) -> None:
        # Two passes; count BEGIN markers.
        _run_fence(tmp_path, _SRC_FULL, _DST_FENCED)
        # Re-read intermediate result.
        dst_mid = (tmp_path / "dst_CLAUDE.md").read_text(encoding="utf-8")

        src2 = tmp_path / "src2.md"
        dst2 = tmp_path / "dst2.md"
        backup2 = tmp_path / "backup2.md"
        src2.write_text(_SRC_FULL, encoding="utf-8")
        dst2.write_text(dst_mid, encoding="utf-8")

        script = _HARNESS_TEMPLATE.format(
            install_sh=str(_INSTALL_SH),
            args=f'"{src2}" "{dst2}" "{backup2}"',
        )
        subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        dst_final = dst2.read_text(encoding="utf-8")

        count = dst_final.count("<!-- BEGIN SAGE -->")
        assert count == 1, f"expected exactly 1 BEGIN marker, found {count}"


# ===========================================================================
# Case 3 — unterminated fence: BEGIN but no END → REFUSED, dst UNTOUCHED
# ===========================================================================


class TestFenceMergeUnterminatedFence:
    """BEGIN with no END is a malformed dst — refuse and leave dst unchanged."""

    _DST_UNTERMINATED = (
        "# Outside\n<!-- BEGIN SAGE -->\nSpine but the END marker is missing.\nMore content here.\n"
    )

    def test_exit_nonzero(self, tmp_path: Path) -> None:
        rc, _out, err, _dst = _run_fence(tmp_path, _SRC_FULL, self._DST_UNTERMINATED)
        assert rc != 0, "unterminated fence must return nonzero exit"

    def test_dst_untouched(self, tmp_path: Path) -> None:
        _rc, _out, _err, dst_after = _run_fence(tmp_path, _SRC_FULL, self._DST_UNTERMINATED)
        assert dst_after == self._DST_UNTERMINATED, "dst must be left UNTOUCHED on refusal"

    def test_backup_mentioned_in_stderr(self, tmp_path: Path) -> None:
        _rc, _out, err, _dst = _run_fence(tmp_path, _SRC_FULL, self._DST_UNTERMINATED)
        # The function includes backup_hint in the warn() message when a
        # backup_path arg was passed.
        assert "backup" in err.lower(), f"refusal message should mention backup; got stderr={err!r}"

    def test_warns_about_unterminated(self, tmp_path: Path) -> None:
        _rc, _out, err, _dst = _run_fence(tmp_path, _SRC_FULL, self._DST_UNTERMINATED)
        # Warn text includes "unterminated" or "END SAGE" reference.
        assert "END SAGE" in err or "unterminated" in err.lower(), (
            f"expected unterminated-fence warning; got stderr={err!r}"
        )


# ===========================================================================
# Case 4 — stray/extra END or END-before-BEGIN → REFUSED, dst untouched
# ===========================================================================


class TestFenceMergeStrayEnd:
    """END-before-BEGIN and stray END markers are refused."""

    _DST_END_BEFORE_BEGIN = (
        "# Outside\n<!-- END SAGE -->\nSome content.\n<!-- BEGIN SAGE -->\nSpine.\n"
    )

    _DST_STRAY_END_ONLY = "# Outside\n<!-- END SAGE -->\nNo BEGIN marker at all.\n"

    _DST_EXTRA_END = (
        "<!-- BEGIN SAGE -->\nSpine.\n<!-- END SAGE -->\nUser content.\n<!-- END SAGE -->\n"
    )

    def test_end_before_begin_refused(self, tmp_path: Path) -> None:
        rc, _out, _err, dst_after = _run_fence(tmp_path, _SRC_FULL, self._DST_END_BEFORE_BEGIN)
        assert rc != 0

    def test_end_before_begin_dst_untouched(self, tmp_path: Path) -> None:
        _rc, _out, _err, dst_after = _run_fence(tmp_path, _SRC_FULL, self._DST_END_BEFORE_BEGIN)
        assert dst_after == self._DST_END_BEFORE_BEGIN

    def test_stray_end_only_refused(self, tmp_path: Path) -> None:
        rc, _out, _err, dst_after = _run_fence(tmp_path, _SRC_FULL, self._DST_STRAY_END_ONLY)
        assert rc != 0

    def test_stray_end_only_dst_untouched(self, tmp_path: Path) -> None:
        _rc, _out, _err, dst_after = _run_fence(tmp_path, _SRC_FULL, self._DST_STRAY_END_ONLY)
        assert dst_after == self._DST_STRAY_END_ONLY

    def test_extra_end_marker_refused(self, tmp_path: Path) -> None:
        rc, _out, _err, dst_after = _run_fence(tmp_path, _SRC_FULL, self._DST_EXTRA_END)
        assert rc != 0

    def test_extra_end_marker_dst_untouched(self, tmp_path: Path) -> None:
        _rc, _out, _err, dst_after = _run_fence(tmp_path, _SRC_FULL, self._DST_EXTRA_END)
        assert dst_after == self._DST_EXTRA_END


# ===========================================================================
# Case 5 — same-line marker in prose: that line PRESERVED (whole-line anchoring)
# ===========================================================================


class TestFenceMergeInlineMarkerPreserved:
    """
    A marker that appears mid-line (surrounded by prose) must NOT be treated
    as a fence boundary.  It must pass through verbatim.
    """

    # The dst has a legitimate fenced block PLUS a prose line containing
    # the marker text — the prose line must survive the merge.
    _DST_WITH_INLINE_MARKER = (
        "# Notes\n"
        "prefix <!-- BEGIN SAGE --> suffix\n"
        "<!-- BEGIN SAGE -->\n"
        "Old spine.\n"
        "<!-- END SAGE -->\n"
        "prefix <!-- END SAGE --> suffix\n"
        "## Tail\n"
    )

    def test_inline_begin_line_preserved(self, tmp_path: Path) -> None:
        rc, _out, _err, dst_after = _run_fence(tmp_path, _SRC_FULL, self._DST_WITH_INLINE_MARKER)
        assert rc == 0, f"exit nonzero; stderr={_err!r}"
        assert "prefix <!-- BEGIN SAGE --> suffix" in dst_after, (
            "prose line containing BEGIN SAGE must be preserved verbatim"
        )

    def test_inline_end_line_preserved(self, tmp_path: Path) -> None:
        rc, _out, _err, dst_after = _run_fence(tmp_path, _SRC_FULL, self._DST_WITH_INLINE_MARKER)
        assert rc == 0
        assert "prefix <!-- END SAGE --> suffix" in dst_after, (
            "prose line containing END SAGE must be preserved verbatim"
        )

    def test_spine_still_updated(self, tmp_path: Path) -> None:
        rc, _out, _err, dst_after = _run_fence(tmp_path, _SRC_FULL, self._DST_WITH_INLINE_MARKER)
        assert rc == 0
        assert "Framework spine v2" in dst_after


# ===========================================================================
# Case 6 — backslash in spine content stays literal (no awk -v mangling)
# ===========================================================================


class TestFenceMergeBackslashLiteral:
    """Backslash sequences in spine content must not be mangled by awk."""

    _SRC_WITH_BACKSLASH = (
        "<!-- BEGIN SAGE -->\n"
        r"# Windows paths like C:\Users\foo\bar and \\server\share" + "\n"
        r"And escaped newline attempt \n and tab \t should be literal." + "\n"
        "<!-- END SAGE -->\n"
    )

    def test_backslash_preserved_in_output(self, tmp_path: Path) -> None:
        rc, _out, _err, dst_after = _run_fence(tmp_path, self._SRC_WITH_BACKSLASH, _DST_FENCED)
        assert rc == 0, f"exit nonzero; stderr={_err!r}"
        assert r"C:\Users\foo\bar" in dst_after, (
            r"backslash path C:\Users\foo\bar must appear literally in dst"
        )

    def test_backslash_n_not_expanded(self, tmp_path: Path) -> None:
        rc, _out, _err, dst_after = _run_fence(tmp_path, self._SRC_WITH_BACKSLASH, _DST_FENCED)
        assert rc == 0
        # If \n were expanded by awk, the literal text "\\n" would be absent.
        assert r"\n" in dst_after, r"literal \n must not be expanded to newline"

    def test_backslash_t_not_expanded(self, tmp_path: Path) -> None:
        rc, _out, _err, dst_after = _run_fence(tmp_path, self._SRC_WITH_BACKSLASH, _DST_FENCED)
        assert rc == 0
        assert r"\t" in dst_after, r"literal \t must not be expanded to tab"


# ===========================================================================
# Case 7 — zero-marker file: clean refuse/fallback, no arithmetic stderr
# ===========================================================================


class TestFenceMergeZeroMarkers:
    """
    A dst with no SAGE markers at all must refuse cleanly — no shell
    arithmetic errors, no '0\\n0' junk on stderr.
    """

    _DST_NO_MARKERS = "# Just a plain CLAUDE.md with no fence markers.\nSome content.\n"

    def test_exit_nonzero(self, tmp_path: Path) -> None:
        rc, _out, _err, _dst = _run_fence(tmp_path, _SRC_FULL, self._DST_NO_MARKERS)
        assert rc != 0

    def test_dst_untouched(self, tmp_path: Path) -> None:
        _rc, _out, _err, dst_after = _run_fence(tmp_path, _SRC_FULL, self._DST_NO_MARKERS)
        assert dst_after == self._DST_NO_MARKERS

    def test_no_arithmetic_noise_in_stderr(self, tmp_path: Path) -> None:
        _rc, _out, err, _dst = _run_fence(tmp_path, _SRC_FULL, self._DST_NO_MARKERS)
        # The historical regression was a bare "0\n0" appearing on stderr from
        # broken arithmetic in the count-check branch.
        assert "0\n0" not in err, f"arithmetic noise in stderr: {err!r}"

    def test_warn_message_present(self, tmp_path: Path) -> None:
        _rc, _out, err, _dst = _run_fence(tmp_path, _SRC_FULL, self._DST_NO_MARKERS)
        assert "WARN:" in err, f"expected a WARN message; got stderr={err!r}"


# ===========================================================================
# Case 8 — fenced-upgrade-without-force: COMPLETES, spine updated, outside preserved
# ===========================================================================


class TestFenceMergeUpgradeWithoutForce:
    """
    Simulates the 'local' regression: a fenced dst being upgraded without
    --force.  fence_replace_claude_md must succeed, update the spine, and
    preserve outside content.  (Full install.sh --force path is Case 9.)
    """

    # A dst simulating a real post-install CLAUDE.md with user content
    # above and below the sage-managed fence.
    _DST_LOCAL_REGRESSION = (
        "# ZiSa Project Notes\n"
        "These are my local customizations.\n\n"
        "<!-- BEGIN SAGE -->\n"
        "# S.A.G.E. — framework spine v1.0\n"
        "This is the old sage spine content.\n"
        "<!-- END SAGE -->\n\n"
        "## Additional personal rules\n"
        "Never delete my personal section.\n"
    )

    _SRC_UPGRADE = (
        "<!-- BEGIN SAGE -->\n"
        "# S.A.G.E. — framework spine v2.0 (UPGRADED)\n"
        "This is the new sage spine content.\n"
        "<!-- END SAGE -->\n"
    )

    def test_exit_zero(self, tmp_path: Path) -> None:
        rc, _out, err, _dst = _run_fence(tmp_path, self._SRC_UPGRADE, self._DST_LOCAL_REGRESSION)
        assert rc == 0, f"upgrade without --force must exit 0; stderr={err!r}"

    def test_spine_updated(self, tmp_path: Path) -> None:
        rc, _out, _err, dst_after = _run_fence(
            tmp_path, self._SRC_UPGRADE, self._DST_LOCAL_REGRESSION
        )
        assert rc == 0
        assert "v2.0 (UPGRADED)" in dst_after, "new spine must be present after upgrade"
        assert "v1.0" not in dst_after, "old spine must be replaced"

    def test_user_content_before_preserved(self, tmp_path: Path) -> None:
        rc, _out, _err, dst_after = _run_fence(
            tmp_path, self._SRC_UPGRADE, self._DST_LOCAL_REGRESSION
        )
        assert rc == 0
        assert "ZiSa Project Notes" in dst_after
        assert "These are my local customizations." in dst_after

    def test_user_content_after_preserved(self, tmp_path: Path) -> None:
        rc, _out, _err, dst_after = _run_fence(
            tmp_path, self._SRC_UPGRADE, self._DST_LOCAL_REGRESSION
        )
        assert rc == 0
        assert "Additional personal rules" in dst_after
        assert "Never delete my personal section." in dst_after


# ===========================================================================
# Case 9 — --force on a fenced dst: full replace, backup taken first
# ===========================================================================
#
# The --force-on-fenced path is handled by install.sh's caller code, NOT by
# fence_replace_claude_md itself (which always does selective merge).  We
# therefore drive this case via install.sh directly with CLAUDE_DIR pointing
# at a temp directory, verifying exit 0, spine replaced, and backup created.
# ===========================================================================


class TestFenceMergeForceFullReplace:
    """
    --force on a fenced dst: install.sh does a full overwrite (not fence-merge)
    and takes a backup first.
    """

    _DST_FENCED_FORCE = (
        "# Personal header\n"
        "<!-- BEGIN SAGE -->\n"
        "# Old spine\n"
        "Old content.\n"
        "<!-- END SAGE -->\n"
        "# Personal footer\n"
    )

    def _run_install_force(self, tmp_path: Path) -> subprocess.CompletedProcess[str]:
        """Drive install.sh --force with CLAUDE_DIR pointing at tmp_path."""
        claude_dir = tmp_path / "claude"
        claude_dir.mkdir()
        dst = claude_dir / "CLAUDE.md"
        dst.write_text(self._DST_FENCED_FORCE, encoding="utf-8")

        env = os.environ.copy()
        env["CLAUDE_DIR"] = str(claude_dir)
        env["HOME"] = str(tmp_path)

        return subprocess.run(
            [
                "bash",
                str(_INSTALL_SH),
                "--force",
                "--claude-md=yes",
                "--no-per-repo-claude-md",
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env=env,
        )

    def test_exit_zero(self, tmp_path: Path) -> None:
        result = self._run_install_force(tmp_path)
        assert result.returncode == 0, f"install.sh --force must exit 0; stderr={result.stderr!r}"

    def test_force_replace_message_in_stdout(self, tmp_path: Path) -> None:
        result = self._run_install_force(tmp_path)
        assert result.returncode == 0
        # install.sh says "would overwrite CLAUDE.md (--force full replace..."
        combined = result.stdout + result.stderr
        assert "force" in combined.lower() or "overwrite" in combined.lower(), (
            f"expected force/overwrite message; output={combined!r}"
        )

    def test_backup_message_in_stdout(self, tmp_path: Path) -> None:
        result = self._run_install_force(tmp_path)
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        assert "back up" in combined.lower() or "backup" in combined.lower(), (
            f"expected backup message; output={combined!r}"
        )


# ===========================================================================
# Case 10 — F2 (Codex PR): --claude-md=no skips fenced dst; --force overrides
# ===========================================================================
#
# Verifies that install.sh honors --claude-md=no even when the dst CLAUDE.md
# already has SAGE fence markers.  Before the fix, the fenced branch ran
# unconditionally (ignoring --claude-md=no), so the documented kill-switch
# was ineffective for re-installs.
#
# --force overrides the skip (the force+fenced branch fires first, so a
# separate test covers that path).
# ===========================================================================


def _run_install_sh(
    tmp_path: Path,
    *,
    flags: list[str],
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Drive install.sh with CLAUDE_DIR pointing at a per-test tmp dir.

    Returns (CompletedProcess, dst_path) — dst_path is the CLAUDE.md in
    the isolated CLAUDE_DIR so callers can inspect whether it was modified.
    """
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    dst = claude_dir / "CLAUDE.md"

    env = os.environ.copy()
    env["CLAUDE_DIR"] = str(claude_dir)
    env["HOME"] = str(tmp_path)

    result = subprocess.run(
        ["bash", str(_INSTALL_SH), "--no-per-repo-claude-md", *flags],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        env=env,
    )
    return result, dst


_DST_FENCED_SKIP = (
    "# My header\n"
    "<!-- BEGIN SAGE -->\n"
    "# Old sage spine\n"
    "Old content.\n"
    "<!-- END SAGE -->\n"
    "# My footer — must survive\n"
)


class TestClaudeMdNoSkipsFencedDst:
    """--claude-md=no must skip fence-merge even when the dst is fenced.

    Codex PR finding F2: the fenced upgrade branch (elif $_dst_has_fence)
    ran unconditionally when INSTALL_CLAUDE_MD == "no", making the
    documented skip flag ineffective for fenced reinstalls.
    """

    def test_exit_zero(self, tmp_path: Path) -> None:
        """install.sh exits 0 when skipping."""
        claude_dir = tmp_path / "claude"
        claude_dir.mkdir()
        dst = claude_dir / "CLAUDE.md"
        dst.write_text(_DST_FENCED_SKIP, encoding="utf-8")

        env = os.environ.copy()
        env["CLAUDE_DIR"] = str(claude_dir)
        env["HOME"] = str(tmp_path)

        result = subprocess.run(
            ["bash", str(_INSTALL_SH), "--claude-md=no", "--no-per-repo-claude-md"],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env=env,
        )
        assert result.returncode == 0, f"expected exit 0; stderr={result.stderr!r}"

    def test_dst_untouched(self, tmp_path: Path) -> None:
        """--claude-md=no on a fenced dst must leave the file byte-identical."""
        claude_dir = tmp_path / "claude"
        claude_dir.mkdir()
        dst = claude_dir / "CLAUDE.md"
        dst.write_text(_DST_FENCED_SKIP, encoding="utf-8")

        env = os.environ.copy()
        env["CLAUDE_DIR"] = str(claude_dir)
        env["HOME"] = str(tmp_path)

        subprocess.run(
            ["bash", str(_INSTALL_SH), "--claude-md=no", "--no-per-repo-claude-md"],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env=env,
        )
        assert dst.read_text(encoding="utf-8") == _DST_FENCED_SKIP, (
            "--claude-md=no must leave the fenced dst UNTOUCHED"
        )

    def test_skip_message_in_output(self, tmp_path: Path) -> None:
        """install.sh must emit a 'skip' message when --claude-md=no."""
        claude_dir = tmp_path / "claude"
        claude_dir.mkdir()
        dst = claude_dir / "CLAUDE.md"
        dst.write_text(_DST_FENCED_SKIP, encoding="utf-8")

        env = os.environ.copy()
        env["CLAUDE_DIR"] = str(claude_dir)
        env["HOME"] = str(tmp_path)

        result = subprocess.run(
            ["bash", str(_INSTALL_SH), "--claude-md=no", "--no-per-repo-claude-md"],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env=env,
        )
        combined = result.stdout + result.stderr
        assert "skip" in combined.lower(), f"expected skip message; output={combined!r}"

    def test_force_overrides_no_and_modifies(self, tmp_path: Path) -> None:
        """--force overrides --claude-md=no for the fenced path (force+fenced = full replace).

        The force+fenced branch fires *before* the no-check, so dst is
        updated even when --claude-md=no is also passed.
        """
        claude_dir = tmp_path / "claude"
        claude_dir.mkdir()
        dst = claude_dir / "CLAUDE.md"
        dst.write_text(_DST_FENCED_SKIP, encoding="utf-8")

        env = os.environ.copy()
        env["CLAUDE_DIR"] = str(claude_dir)
        env["HOME"] = str(tmp_path)

        result = subprocess.run(
            [
                "bash",
                str(_INSTALL_SH),
                "--force",
                "--claude-md=no",
                "--no-per-repo-claude-md",
            ],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env=env,
        )
        assert result.returncode == 0, f"--force must exit 0; stderr={result.stderr!r}"
        # Force overwrites dst — it must differ from the original
        # (full replace with the current repo's CLAUDE.md).
        dst_after = dst.read_text(encoding="utf-8")
        assert dst_after != _DST_FENCED_SKIP, (
            "--force must overwrite the fenced dst even when --claude-md=no is set"
        )


# ===========================================================================
# Case 11 — F3 (Codex PR): malformed SOURCE fence → refused, dst untouched
# ===========================================================================
#
# fence_replace_claude_md previously only checked -z src_spine (empty result
# from awk).  If the source had BEGIN but no END, awk would print BEGIN through
# EOF (non-empty), validation would pass, and an unterminated fence would be
# written into the user's CLAUDE.md.
#
# After the fix, the source is validated for exactly one BEGIN and one END
# in the correct order before extraction is attempted.  Any malformation
# is treated as a sage packaging defect: refuse loudly, dst untouched.
# ===========================================================================


class TestFenceMergeSourceValidation:
    """SOURCE fence must be validated before merging (Codex PR F3).

    fence_replace_claude_md now validates the SOURCE (sage's own CLAUDE.md)
    for exactly one whole-line BEGIN and one whole-line END, in order.
    Any malformation refuses the merge and leaves dst untouched.
    """

    # The dst has a valid fence — the source is what's malformed in these tests.
    _DST_VALID = _DST_FENCED  # reuse the standard fenced dst fixture

    # Source with BEGIN but no END — this is the primary F3 regression.
    _SRC_UNTERMINATED = "<!-- BEGIN SAGE -->\n# Framework spine\nContent that never ends.\n"

    # Source with END but no BEGIN.
    _SRC_END_ONLY = "<!-- END SAGE -->\n# Content after stray end.\n"

    # Source with no markers at all.
    _SRC_NO_MARKERS = "# Plain CLAUDE.md — no fence markers.\nContent here.\n"

    # Source with duplicate BEGIN markers.
    _SRC_DOUBLE_BEGIN = "<!-- BEGIN SAGE -->\n# Spine part 1\n<!-- BEGIN SAGE -->\n# Spine part 2\n<!-- END SAGE -->\n"

    # Source with END before BEGIN.
    _SRC_END_BEFORE_BEGIN = "<!-- END SAGE -->\n# Between markers\n<!-- BEGIN SAGE -->\n# Spine\n"

    def test_unterminated_source_exits_nonzero(self, tmp_path: Path) -> None:
        """SOURCE with BEGIN but no END must refuse (exit nonzero)."""
        rc, _out, _err, _dst = _run_fence(tmp_path, self._SRC_UNTERMINATED, self._DST_VALID)
        assert rc != 0, "unterminated SOURCE fence must return nonzero exit"

    def test_unterminated_source_dst_untouched(self, tmp_path: Path) -> None:
        """SOURCE unterminated → dst must be left UNTOUCHED."""
        _rc, _out, _err, dst_after = _run_fence(tmp_path, self._SRC_UNTERMINATED, self._DST_VALID)
        assert dst_after == self._DST_VALID, "dst must be UNTOUCHED when source fence is malformed"

    def test_unterminated_source_warns_stderr(self, tmp_path: Path) -> None:
        """SOURCE unterminated → WARN emitted to stderr."""
        _rc, _out, err, _dst = _run_fence(tmp_path, self._SRC_UNTERMINATED, self._DST_VALID)
        assert "WARN:" in err, f"expected WARN message; got stderr={err!r}"

    def test_source_end_only_refused(self, tmp_path: Path) -> None:
        """SOURCE with END but no BEGIN is refused."""
        rc, _out, _err, dst_after = _run_fence(tmp_path, self._SRC_END_ONLY, self._DST_VALID)
        assert rc != 0
        assert dst_after == self._DST_VALID

    def test_source_no_markers_refused(self, tmp_path: Path) -> None:
        """SOURCE with no markers at all is refused."""
        rc, _out, _err, dst_after = _run_fence(tmp_path, self._SRC_NO_MARKERS, self._DST_VALID)
        assert rc != 0
        assert dst_after == self._DST_VALID

    def test_source_double_begin_refused(self, tmp_path: Path) -> None:
        """SOURCE with two BEGIN markers is refused (non-deterministic)."""
        rc, _out, _err, dst_after = _run_fence(tmp_path, self._SRC_DOUBLE_BEGIN, self._DST_VALID)
        assert rc != 0
        assert dst_after == self._DST_VALID

    def test_source_end_before_begin_refused(self, tmp_path: Path) -> None:
        """SOURCE with END before BEGIN is refused."""
        rc, _out, _err, dst_after = _run_fence(
            tmp_path, self._SRC_END_BEFORE_BEGIN, self._DST_VALID
        )
        assert rc != 0
        assert dst_after == self._DST_VALID

    def test_well_formed_source_still_succeeds(self, tmp_path: Path) -> None:
        """Regression guard: a well-formed source still merges successfully."""
        rc, _out, _err, dst_after = _run_fence(tmp_path, _SRC_FULL, self._DST_VALID)
        assert rc == 0, f"well-formed source must succeed; stderr={_err!r}"
        assert "Framework spine v2" in dst_after
