"""Tests for sage.ignore — GitignoreMatcher."""

from __future__ import annotations


def test_gitignore_matcher_edge_cases(tmp_path):
    """GitignoreMatcher handles escaped chars, empty patterns, and path operations."""
    from sage_mcp.ignore import GitignoreMatcher

    # Escaped '#' at start — not a comment but a literal #
    ignore_file = tmp_path / ".gitignore"
    ignore_file.write_text(
        "# comment line\n"
        "\\#not_a_comment\n"  # escaped hash → literal pattern "#not_a_comment"
        "\\!not_negated\n"  # escaped ! → literal pattern
        "\n"  # empty line skipped
        "/\n"  # just a slash → empty after strip → skipped (line 68)
        "*.log\n"
        "!keep.log\n"
        "docs/\n"  # dir-only
        "**/nested\n"  # globstar
    )

    matcher = GitignoreMatcher.from_dir(tmp_path)
    assert matcher is not None

    # matches() on a path outside base_dir returns None (ValueError path)
    outside_path = tmp_path.parent / "outside.txt"
    result = matcher.matches(outside_path)
    assert result is None

    # matches() on the base_dir itself (relative = "") returns None
    result2 = matcher.matches(tmp_path)
    assert result2 is None

    # *.log matches
    log_file = tmp_path / "server.log"
    log_file.touch()
    assert matcher.matches(log_file) is True

    # !keep.log negates
    keep_log = tmp_path / "keep.log"
    keep_log.touch()
    assert matcher.matches(keep_log) is False

    # docs/ matches a directory
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    assert matcher.matches(docs_dir, is_dir=True) is True

    # **/nested matches across depth
    nested_dir = tmp_path / "a" / "b" / "nested"
    nested_dir.mkdir(parents=True)
    assert matcher.matches(nested_dir) is True


def test_gitignore_matcher_no_file_returns_none(tmp_path):
    """from_dir returns None when the ignore file doesn't exist."""
    from sage_mcp.ignore import GitignoreMatcher

    result = GitignoreMatcher.from_dir(tmp_path, ".nonexistent_ignore")
    assert result is None
