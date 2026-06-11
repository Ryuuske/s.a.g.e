"""Tests for diary_ingest paths (findings [20][38][40], pycore audit cluster).

Tests cover:
  1. Early-return: nonexistent diary_dir → {"days_updated": 0, "closets_created": 0}
  2. Early-return: dir with no .md files → same
  3. delete()-exception-swallow on force rebuild: exception is not propagated;
     the upsert path still runs and days_updated > 0 (findings [38][40])
  4. Corrupt-state-file recovery: json.loads failure silently resets to {} so
     ingest continues normally (finding [35])
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from sage_mcp.diary_ingest import ingest_diaries


class TestIngestDiariesEarlyGuards:
    """ingest_diaries returns zero-counts without touching the nook on degenerate inputs."""

    def test_nonexistent_diary_dir_returns_zero(self, tmp_path):
        """Passing a directory path that does not exist returns zero-counts immediately."""
        missing = tmp_path / "no_such_diary_dir"
        nook_path = str(tmp_path / "fake_nook")
        result = ingest_diaries(str(missing), nook_path)
        assert result == {"days_updated": 0, "closets_created": 0}

    def test_empty_diary_dir_returns_zero(self, tmp_path):
        """An existing diary directory with no .md files returns zero-counts immediately."""
        empty_dir = tmp_path / "empty_diary"
        empty_dir.mkdir()
        # Add a non-.md file so the directory is not literally empty on disk
        (empty_dir / "README.txt").write_text("not a diary")
        nook_path = str(tmp_path / "fake_nook")
        result = ingest_diaries(str(empty_dir), nook_path)
        assert result == {"days_updated": 0, "closets_created": 0}

    def test_result_shape_has_expected_keys(self, tmp_path):
        """Return value always has exactly 'days_updated' and 'closets_created'."""
        missing = tmp_path / "no_such_dir"
        nook_path = str(tmp_path / "fake_nook")
        result = ingest_diaries(str(missing), nook_path)
        assert set(result.keys()) == {"days_updated", "closets_created"}


def _make_diary_file(diary_dir, date_str="2025-01-15"):
    """Write a minimal valid diary file (> 50 chars, ## entry header)."""
    content = "## Morning check-in\nToday I worked on the sage project and made great progress.\n"
    path = diary_dir / f"{date_str}.md"
    path.write_text(content)
    return path


@contextmanager
def _mock_nook(drawers_col=None, closets_col=None):
    """Context manager that patches all nook I/O in diary_ingest to avoid ChromaDB."""
    if drawers_col is None:
        drawers_col = MagicMock()
        drawers_col.upsert = MagicMock()
        drawers_col.delete = MagicMock()
    if closets_col is None:
        closets_col = MagicMock()
        closets_col.upsert = MagicMock()

    with (
        patch("sage_mcp.diary_ingest.get_collection", return_value=drawers_col),
        patch("sage_mcp.diary_ingest.get_closets_collection", return_value=closets_col),
        patch(
            "sage_mcp.diary_ingest.mine_lock",
            return_value=MagicMock(__enter__=lambda s: s, __exit__=MagicMock(return_value=False)),
        ),
        patch("sage_mcp.diary_ingest.build_closet_lines", return_value=["line1"]),
        patch("sage_mcp.diary_ingest.upsert_closet_lines", return_value=1),
        patch("sage_mcp.diary_ingest.purge_file_closets"),
        patch("sage_mcp.diary_ingest._write_state_atomic"),
    ):
        yield drawers_col, closets_col


class TestIngestDiariesDeleteExceptionSwallow:
    """findings [38][40]: delete() exception on force rebuild must not propagate."""

    def test_delete_exception_does_not_propagate_on_force(self, tmp_path):
        """drawers_col.delete() raising RuntimeError must be swallowed on force=True."""
        diary_dir = tmp_path / "diary"
        diary_dir.mkdir()
        _make_diary_file(diary_dir)
        nook_path = str(tmp_path / "nook")

        drawers_col = MagicMock()
        drawers_col.delete = MagicMock(side_effect=RuntimeError("backend locked"))
        drawers_col.upsert = MagicMock()

        with _mock_nook(drawers_col=drawers_col):
            # Must not raise even though delete() raises.
            result = ingest_diaries(str(diary_dir), nook_path, force=True)

        assert result["days_updated"] >= 1, "rebuild should complete despite delete() error"

    def test_upsert_still_called_when_delete_raises(self, tmp_path):
        """Even after delete() raises, the batch upsert for the current day runs."""
        diary_dir = tmp_path / "diary"
        diary_dir.mkdir()
        _make_diary_file(diary_dir)
        nook_path = str(tmp_path / "nook")

        drawers_col = MagicMock()
        drawers_col.delete = MagicMock(side_effect=RuntimeError("I/O error"))
        drawers_col.upsert = MagicMock()

        with _mock_nook(drawers_col=drawers_col):
            ingest_diaries(str(diary_dir), nook_path, force=True)

        drawers_col.upsert.assert_called_once()


class TestIngestDiariesCorruptStateFile:
    """finding [35]: corrupt state file silently resets to {} so ingest continues."""

    def test_corrupt_state_file_silently_resets(self, tmp_path):
        """A state file with invalid JSON causes state={} (not an exception)."""
        diary_dir = tmp_path / "diary"
        diary_dir.mkdir()
        _make_diary_file(diary_dir)
        nook_path = str(tmp_path / "nook")

        # Write a corrupt state file in the expected location.
        import hashlib

        state_root = tmp_path / ".sage" / "state"
        state_root.mkdir(parents=True)
        key = hashlib.sha256(f"{nook_path}|{diary_dir.resolve()}".encode()).hexdigest()[:24]
        state_file = state_root / f"diary_ingest_{key}.json"
        state_file.write_text("{this is not valid json")

        with _mock_nook():
            # Patch expanduser so the state dir resolves to our tmp tree.
            with patch("os.path.expanduser", return_value=str(tmp_path)):
                # Should not raise — falls back to state={}.
                result = ingest_diaries(str(diary_dir), nook_path)

        assert isinstance(result, dict)
