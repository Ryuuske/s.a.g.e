"""Tests for sage.nook shared helpers."""

import chromadb

from sage_mcp.backends import CollectionNotInitializedError, NookNotFoundError
from sage_mcp.nook import _open_collection_or_explain, get_collection


def _capture():
    """Return (emit, lines) — emit appends to lines for inspection."""
    lines: list[str] = []
    return lines.append, lines


def test_open_collection_or_explain_state_a_missing_dir(tmp_path):
    """State A: nook dir does not exist."""
    emit, lines = _capture()
    missing = tmp_path / "no-such-nook"

    result = _open_collection_or_explain(str(missing), out=emit)

    assert result is None
    assert any("No nook found" in line for line in lines)
    assert any("sage init" in line for line in lines)
    # Helper must not create the directory.
    assert not missing.exists()


def test_open_collection_or_explain_state_b_no_db(tmp_path):
    """State B: dir exists but chroma.sqlite3 does not.

    Critical invariant: the helper must NOT trigger chromadb's lazy DB
    creation by reaching the backend. The dir must remain empty after
    the call so a read-only inspection stays read-only.
    """
    emit, lines = _capture()
    nook = tmp_path / "nook"
    nook.mkdir()
    assert not (nook / "chroma.sqlite3").exists()

    result = _open_collection_or_explain(str(nook), out=emit)

    assert result is None
    assert any("has no chroma.sqlite3 yet" in line for line in lines)
    # No side-effect: backend was not invoked.
    assert list(nook.iterdir()) == []


def test_open_collection_or_explain_state_c_no_collection(tmp_path):
    """State C: DB file exists but the collection has never been created."""
    emit, lines = _capture()
    nook = tmp_path / "nook"
    nook.mkdir()
    chromadb.PersistentClient(path=str(nook))  # creates DB, no collection
    assert (nook / "chroma.sqlite3").is_file()

    result = _open_collection_or_explain(str(nook), out=emit)

    assert result is None
    assert any("initialized but empty" in line for line in lines)
    assert any("sage mine" in line for line in lines)


def test_open_collection_or_explain_state_d_healthy(tmp_path):
    """State D: healthy nook — returns the opened collection silently."""
    emit, lines = _capture()
    nook = tmp_path / "nook"
    nook.mkdir()
    get_collection(str(nook), create=True)  # bootstrap collection

    result = _open_collection_or_explain(str(nook), out=emit)

    assert result is not None
    assert lines == []  # healthy path is silent


def test_open_collection_or_explain_state_e_unexpected_error(tmp_path, monkeypatch):
    """State E: unexpected error opening the backend routes to repair hint."""
    emit, lines = _capture()
    nook = tmp_path / "nook"
    nook.mkdir()
    (nook / "chroma.sqlite3").touch()  # pass the isfile guard

    def boom(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("sage_mcp.nook.get_collection", boom)

    result = _open_collection_or_explain(str(nook), out=emit)

    assert result is None
    assert any("Error opening nook" in line for line in lines)
    assert any("repair-status" in line for line in lines)


def test_open_collection_or_explain_default_sink_is_print(tmp_path, capsys):
    """When out is None, messages go through builtin print → stdout."""
    missing = tmp_path / "no-such-nook"

    result = _open_collection_or_explain(str(missing))

    assert result is None
    assert "No nook found" in capsys.readouterr().out


def test_open_collection_or_explain_propagates_nook_not_found_from_backend(tmp_path, monkeypatch):
    """If the backend raises bare NookNotFoundError after our filesystem
    guards (rare race or backend-internal "not found"), the helper still
    prints the State A message and returns None."""
    emit, lines = _capture()
    nook = tmp_path / "nook"
    nook.mkdir()
    (nook / "chroma.sqlite3").touch()

    def raise_pnf(*args, **kwargs):
        raise NookNotFoundError(str(nook))

    monkeypatch.setattr("sage_mcp.nook.get_collection", raise_pnf)

    result = _open_collection_or_explain(str(nook), out=emit)

    assert result is None
    assert any("No nook found" in line for line in lines)


def test_open_collection_or_explain_reraises_backend_closed_error(tmp_path, monkeypatch):
    """BackendClosedError is a programmer error (caller violated the backend
    lifecycle), not a nook-state UX condition. The helper must propagate
    it instead of swallowing it into the State E "repair-status" hint.

    Without this re-raise, a closed default backend would silently mask
    every call site as "Error opening nook ... Try: repair-status"
    even when the actual fix is to stop using a closed backend handle.
    """
    from sage_mcp.backends import BackendClosedError

    nook = tmp_path / "nook"
    nook.mkdir()
    (nook / "chroma.sqlite3").touch()

    def raise_closed(*args, **kwargs):
        raise BackendClosedError("ChromaBackend has been closed")

    monkeypatch.setattr("sage_mcp.nook.get_collection", raise_closed)

    import pytest

    with pytest.raises(BackendClosedError):
        _open_collection_or_explain(str(nook))


def test_open_collection_or_explain_distinguishes_collection_subclass(tmp_path, monkeypatch):
    """The helper must surface CollectionNotInitializedError as the
    'empty' message rather than the broader 'No nook found' message,
    even though the former subclasses the latter."""
    emit, lines = _capture()
    nook = tmp_path / "nook"
    nook.mkdir()
    (nook / "chroma.sqlite3").touch()

    def raise_cnie(*args, **kwargs):
        raise CollectionNotInitializedError(str(nook))

    monkeypatch.setattr("sage_mcp.nook.get_collection", raise_cnie)

    result = _open_collection_or_explain(str(nook), out=emit)

    assert result is None
    assert any("initialized but empty" in line for line in lines)
    assert not any("No nook found" in line for line in lines)
