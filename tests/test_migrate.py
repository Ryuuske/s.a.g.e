"""Tests for destructive-operation safety in sage.migrate."""

import os
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from sage_mcp.migrate import (
    _restore_stale_nook,
    collection_write_roundtrip_works,
    extract_drawers_from_sqlite,
    migrate,
)


def test_migrate_requires_nook_database(tmp_path, capsys):
    nook_dir = tmp_path / "nook"
    nook_dir.mkdir()

    result = migrate(str(nook_dir))

    out = capsys.readouterr().out
    assert result is False
    assert "No nook database found" in out


def test_migrate_aborts_without_confirmation(tmp_path, capsys):
    nook_dir = tmp_path / "nook"
    nook_dir.mkdir()
    # Presence of chroma.sqlite3 is the safety gate; validity is mocked below.
    (nook_dir / "chroma.sqlite3").write_text("db")

    mock_chromadb = SimpleNamespace(
        __version__="0.6.0",
        PersistentClient=MagicMock(side_effect=Exception("unreadable")),
    )

    with (
        patch.dict("sys.modules", {"chromadb": mock_chromadb}),
        patch("sage_mcp.migrate.detect_chromadb_version", return_value="0.5.x"),
        patch(
            "sage_mcp.migrate.extract_drawers_from_sqlite",
            return_value=[{"id": "id1", "document": "doc", "metadata": {"wing": "w", "room": "r"}}],
        ),
        patch("builtins.input", return_value="n"),
        patch("sage_mcp.migrate.shutil.copytree") as mock_copytree,
        patch("sage_mcp.migrate.shutil.rmtree") as mock_rmtree,
    ):
        result = migrate(str(nook_dir))

    out = capsys.readouterr().out
    assert result is False
    assert "Aborted." in out
    mock_copytree.assert_not_called()
    mock_rmtree.assert_not_called()


def test_restore_stale_nook_with_clean_destination(tmp_path):
    """Rollback when no partial copy exists at nook_path."""
    nook_path = tmp_path / "nook"
    stale_path = tmp_path / "nook.old"
    stale_path.mkdir()
    (stale_path / "chroma.sqlite3").write_bytes(b"original")

    _restore_stale_nook(str(nook_path), str(stale_path))

    assert nook_path.is_dir()
    assert (nook_path / "chroma.sqlite3").read_bytes() == b"original"
    assert not stale_path.exists()


def test_restore_stale_nook_clears_partial_copy(tmp_path):
    """Rollback must remove a partially-copied nook_path before restoring.

    Simulates the Qodo-reported hazard: shutil.move() began creating
    nook_path, then failed. A bare os.replace(stale, nook_path) would
    trip on the existing destination; _restore_stale_nook must clear it.
    """
    nook_path = tmp_path / "nook"
    stale_path = tmp_path / "nook.old"

    stale_path.mkdir()
    (stale_path / "chroma.sqlite3").write_bytes(b"original")

    nook_path.mkdir()
    (nook_path / "half-copied.bin").write_bytes(b"garbage")

    _restore_stale_nook(str(nook_path), str(stale_path))

    assert nook_path.is_dir()
    assert (nook_path / "chroma.sqlite3").read_bytes() == b"original"
    assert not (nook_path / "half-copied.bin").exists()
    assert not stale_path.exists()


def test_restore_stale_nook_logs_and_swallows_on_failure(tmp_path, capsys):
    """If restore itself fails, log both paths — don't raise from rollback."""
    nook_path = tmp_path / "nook"
    stale_path = tmp_path / "nook.old"
    stale_path.mkdir()

    # Force os.replace to fail deterministically.
    with patch("sage_mcp.migrate.os.replace", side_effect=OSError("boom")):
        _restore_stale_nook(str(nook_path), str(stale_path))

    out = capsys.readouterr().out
    assert "CRITICAL" in out
    assert os.fspath(nook_path) in out
    assert os.fspath(stale_path) in out


class _FakeGetResult:
    def __init__(self, ids):
        self.ids = ids


class _WritableFakeCollection:
    def __init__(self):
        self.ids = set()
        self.deleted = []

    def upsert(self, *, ids, documents, metadatas):
        self.ids.update(ids)

    def get(self, *, ids, include=None):
        return _FakeGetResult([drawer_id for drawer_id in ids if drawer_id in self.ids])

    def delete(self, *, ids=None, where=None):
        for drawer_id in ids or []:
            self.ids.discard(drawer_id)
            self.deleted.append(drawer_id)


class _SilentWriteDropCollection(_WritableFakeCollection):
    def upsert(self, *, ids, documents, metadatas):
        return None


class _SilentDeleteDropCollection(_WritableFakeCollection):
    def delete(self, *, ids=None, where=None):
        self.deleted.extend(ids or [])


def test_collection_write_roundtrip_works_when_probe_persists_and_deletes():
    col = _WritableFakeCollection()

    assert collection_write_roundtrip_works(col) is True
    assert col.ids == set()
    assert len(col.deleted) == 1


def test_collection_write_roundtrip_fails_when_upsert_silently_drops():
    col = _SilentWriteDropCollection()

    assert collection_write_roundtrip_works(col) is False
    assert col.ids == set()


def test_collection_write_roundtrip_fails_when_delete_silently_drops():
    col = _SilentDeleteDropCollection()

    assert collection_write_roundtrip_works(col) is False
    assert len(col.ids) == 1


def _make_minimal_chromadb_sqlite(tmp_path):
    """Build a SQLite file with the minimal schema extract_drawers_from_sqlite reads."""
    db = tmp_path / "chroma.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE embeddings (id INTEGER PRIMARY KEY, embedding_id TEXT);
        CREATE TABLE embedding_metadata (
            id INTEGER, key TEXT,
            string_value TEXT, int_value INTEGER,
            float_value REAL, bool_value INTEGER
        );
        INSERT INTO embeddings VALUES (1, 'd-001');
        INSERT INTO embedding_metadata VALUES (1, 'chroma:document', 'hello', NULL, NULL, NULL);
        INSERT INTO embedding_metadata VALUES (1, 'wing', 'personal', NULL, NULL, NULL);
        INSERT INTO embedding_metadata VALUES (1, 'room', '2026-04-26', NULL, NULL, NULL);
        """
    )
    conn.commit()
    conn.close()
    return str(db)


def test_extract_drawers_returns_drawers(tmp_path):
    db_path = _make_minimal_chromadb_sqlite(tmp_path)
    drawers = extract_drawers_from_sqlite(db_path)
    assert len(drawers) == 1
    assert drawers[0]["id"] == "d-001"
    assert drawers[0]["document"] == "hello"
    assert drawers[0]["metadata"] == {"wing": "personal", "room": "2026-04-26"}


def test_migrate_dry_run_rebuilds_when_collection_is_readable_but_not_writable(tmp_path, capsys):
    nook_dir = tmp_path / "nook"
    nook_dir.mkdir()
    (nook_dir / "chroma.sqlite3").write_text("db")

    fake_col = MagicMock()
    fake_col.count.return_value = 102

    drawers = [
        {
            "id": "id1",
            "document": "hello",
            "metadata": {"wing": "test-wing", "room": "general"},
        }
    ]

    with (
        patch("sage_mcp.migrate.detect_chromadb_version", return_value="1.x"),
        patch("sage_mcp.backends.chroma.ChromaBackend") as mock_backend,
        patch(
            "sage_mcp.migrate.collection_write_roundtrip_works", return_value=False
        ) as mock_probe,
        patch("sage_mcp.migrate.extract_drawers_from_sqlite", return_value=drawers) as mock_extract,
    ):
        mock_backend.backend_version.return_value = "1.5.8"
        mock_backend.return_value.get_collection.return_value = fake_col

        result = migrate(str(nook_dir), dry_run=True)

    out = capsys.readouterr().out

    assert result is True
    mock_probe.assert_called_once_with(fake_col)
    mock_extract.assert_called_once_with(
        os.path.join(os.path.abspath(os.fspath(nook_dir)), "chroma.sqlite3")
    )

    assert "readable by chromadb 1.5.8, but write/delete verification failed" in out
    assert "Rebuilding from SQLite" in out
    assert "Extracted 1 drawers from SQLite" in out
    assert "DRY RUN" in out


def test_migrate_cleans_temp_nook_on_chromadb_failure(tmp_path):
    """If chromadb fails after the temp nook is created, mkdtemp's
    directory must be removed — without try/finally it leaked into the
    system temp root forever."""
    import tempfile as _tempfile

    nook_dir = tmp_path / "nook"
    nook_dir.mkdir()
    (nook_dir / "chroma.sqlite3").write_text("db")

    captured_temp_paths = []
    real_mkdtemp = _tempfile.mkdtemp

    def tracking_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        captured_temp_paths.append(path)
        return path

    failing_backend = MagicMock()
    # First ChromaBackend().get_collection() must raise so we drop into
    # the SQL-extraction path; the second ChromaBackend().get_or_create_collection()
    # raises to trigger the cleanup we are testing.
    failing_backend.get_collection.side_effect = Exception("unreadable")
    failing_backend.get_or_create_collection.side_effect = RuntimeError("chromadb boom")

    import sage_mcp.backends.chroma as _chroma_mod

    with (
        patch("sage_mcp.migrate.detect_chromadb_version", return_value="0.5.x"),
        patch(
            "sage_mcp.migrate.extract_drawers_from_sqlite",
            return_value=[{"id": "id1", "document": "doc", "metadata": {"wing": "w", "room": "r"}}],
        ),
        patch("builtins.input", return_value="y"),
        patch("sage_mcp.migrate.shutil.copytree"),
        patch("sage_mcp.migrate.tempfile.mkdtemp", side_effect=tracking_mkdtemp),
        patch.object(_chroma_mod, "ChromaBackend", return_value=failing_backend),
    ):
        try:
            migrate(str(nook_dir), confirm=True)
        except Exception:
            pass

    assert captured_temp_paths, "mkdtemp was never called — flow short-circuited"
    for p in captured_temp_paths:
        assert not os.path.exists(p), f"temp nook was not cleaned up: {p}"


# ── Pass 3/4: _migrate_lock locked-flag (Cat 14 F4 regression) ────────


@pytest.mark.skipif(os.name == "nt", reason="fcntl not available on Windows")
def test_migrate_lock_contender_does_not_unlink_lock_file(tmp_path):
    """A second migrator whose flock raises BlockingIOError must NOT unlink
    the lock file path. Otherwise a third process would create a fresh
    inode and acquire its own flock, defeating the lock.
    """
    import fcntl
    import os as _os
    from sage_mcp.migrate import _migrate_lock

    nook = tmp_path / "nook"
    nook.mkdir()
    lock_path = str(nook).rstrip("/") + ".migrate.lock"

    # Wrap the holder lifecycle in try/finally so a failed assertion below
    # still releases the flock and removes the file — otherwise the leak
    # would corrupt subsequent tests in the same pytest process.
    holder_fh = open(lock_path, "w")
    try:
        fcntl.flock(holder_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        import pytest as _pytest

        with _pytest.raises(RuntimeError, match="Another sage migrate"):
            with _migrate_lock(str(nook)):
                raise AssertionError("contender should not enter")

        # Lock file MUST still exist on disk — the holder owns it.
        assert _os.path.exists(lock_path), (
            "contender unlinked the lock file out from under the holder; "
            "a third migrator would now create a fresh inode and bypass the lock"
        )
    finally:
        try:
            fcntl.flock(holder_fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        holder_fh.close()
        try:
            _os.unlink(lock_path)
        except OSError:
            pass


@pytest.mark.skipif(os.name == "nt", reason="fcntl not available on Windows")
def test_migrate_lock_holder_cleans_up_on_exit(tmp_path):
    """When this caller successfully acquires the lock, the lock file should
    be removed on exit so a subsequent migrate against the same nook can
    re-acquire."""
    import os as _os
    from sage_mcp.migrate import _migrate_lock

    nook = tmp_path / "nook"
    nook.mkdir()
    lock_path = str(nook).rstrip("/") + ".migrate.lock"

    with _migrate_lock(str(nook)):
        assert _os.path.exists(lock_path)

    assert not _os.path.exists(lock_path), "holder did not clean up its own lock file"


@pytest.mark.skipif(os.name != "nt", reason="Windows msvcrt branch only")
def test_migrate_lock_windows_contender_raises(tmp_path):
    """On Windows, a second migrator must raise RuntimeError when the lock is held.

    Covers the os.name == 'nt' branch in _migrate_lock introduced by the
    cross-platform fix (Finding 72 cross-platform). Uses msvcrt.locking to
    hold the lock, then verifies _migrate_lock raises with the expected message.
    """
    import msvcrt
    import os as _os
    from sage_mcp.migrate import _migrate_lock

    nook = tmp_path / "nook"
    nook.mkdir()
    lock_path = str(nook).rstrip("/") + ".migrate.lock"

    holder_fh = open(lock_path, "w")
    try:
        # Acquire the lock as the holder via msvcrt.locking.
        msvcrt.locking(holder_fh.fileno(), msvcrt.LK_NBLCK, 1)

        with pytest.raises(RuntimeError, match="Another sage migrate"):
            with _migrate_lock(str(nook)):
                raise AssertionError("contender should not enter")

        assert _os.path.exists(lock_path), (
            "contender unlinked the lock file out from under the holder"
        )
    finally:
        try:
            msvcrt.locking(holder_fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        holder_fh.close()
        try:
            _os.unlink(lock_path)
        except OSError:
            pass
