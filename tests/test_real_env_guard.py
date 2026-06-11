"""Unit tests for the ADR-0095 real-env guard helpers in conftest.py.

The guard itself is session-scoped infrastructure (exercised by every suite
run); these tests pin the path-classification and snapshot semantics it
depends on (PR #42 review P2-2/P2-3).
"""

from pathlib import Path

from conftest import _classify_sage_path, _snapshot_sage_tree


# ── Classification ───────────────────────────────────────────────────────────


def test_live_db_paths_classified():
    for p in (
        "nook/chroma.sqlite3",
        "knowledge_graph.sqlite3",
    ):
        assert _classify_sage_path(p) == "live_db", p


def test_json_store_paths_classified():
    """Small JSON memory stores are content-hash compared (PR #46 review) —
    a similar-size overwrite must not pass as live-db growth."""
    for p in (
        "hallways.json",
        "tunnels.json",
        "known_entities.json",
        "entity_registry.json",
    ):
        assert _classify_sage_path(p) == "json_store", p


def test_churn_paths_classified():
    for p in (
        "telemetry/turns.jsonl",
        "wal/write_log.jsonl",
        "locks/abc123.lock",
        "hook_state/hook.log",
        "state/diary_ingest_x.json",
        "nook/e3ed87a5-1234/data_level0.bin",
        "nook.backup/chroma.sqlite3",
        "quarantine-20260609/nook.empty/chroma.sqlite3",
        "current_wing",
        "last_keeper_dispatch",
        "tier0-last-hash-sage.txt",
        "autonomy-run.json",
        "autonomy-reporting-eval.log",
        # bare directory entries (now snapshotted — PR #46 review)
        "telemetry",
        "wal",
        "locks",
        "hook_state",
        "state",
        "nook",
        "nook.backup",
        "quarantine-20260609",
        "nook/e3ed87a5-1234",
    ):
        assert _classify_sage_path(p) == "churn", p


def test_static_paths_classified():
    for p in (
        "config.json",
        "identity.txt",
        "wing_config.json",
        "kg.sqlite",
        "people_map.json",
        "estate-workshop-ledger.json",
        "estate-revision.json",
        "some-novel-file.bin",  # UNKNOWN defaults to static semantics
    ):
        assert _classify_sage_path(p) == "static", p


def test_chroma_sqlite_is_live_db_not_nook_churn():
    """nook/chroma.sqlite3 must hit the LIVE_DB carve-out before the nook/* glob."""
    assert _classify_sage_path("nook/chroma.sqlite3") == "live_db"
    assert _classify_sage_path("nook/other.sqlite3") == "churn"


# ── Snapshot semantics ───────────────────────────────────────────────────────


def test_snapshot_does_not_follow_symlinks(tmp_path):
    """A symlink inside the store must be lstat'ed, never followed — an external
    target tree must not enter the diff; the link is recorded by its target."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big.bin").write_bytes(b"x" * 4096)
    store = tmp_path / "store"
    store.mkdir()
    (store / "real.json").write_text("{}", encoding="utf-8")
    (store / "link").symlink_to(outside)
    snap = _snapshot_sage_tree(store)
    assert snap is not None
    assert "real.json" in snap
    assert not any(k.startswith("link/") for k in snap), (
        "symlinked external tree leaked into the snapshot"
    )
    kind, target, _ = snap["link"]
    assert kind == "link" and target == str(outside), (
        "dir-symlink must be recorded by its own target (retarget detection)"
    )


def test_snapshot_records_directories(tmp_path):
    """Empty directories are snapshot entries (PR #46 review): creating or
    deleting one inside the real store must be visible to the guard."""
    store = tmp_path / "store"
    (store / "empty-dir").mkdir(parents=True)
    snap = _snapshot_sage_tree(store)
    assert snap is not None
    assert snap["empty-dir"][0] == "dir"


def test_snapshot_relpaths_are_posix_style(tmp_path):
    store = tmp_path / "store"
    (store / "sub").mkdir(parents=True)
    (store / "sub" / "f.txt").write_text("x", encoding="utf-8")
    snap = _snapshot_sage_tree(store)
    assert snap is not None and "sub/f.txt" in snap and "sub" in snap


def test_snapshot_records_size_and_mtime(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    f = store / "a.bin"
    f.write_bytes(b"abcd")
    snap = _snapshot_sage_tree(store)
    assert snap is not None
    kind, size, mtime = snap["a.bin"]
    st = Path(f).lstat()
    assert kind == "file"
    assert size == float(st.st_size) == 4.0
    assert mtime == float(st.st_mtime)


def test_snapshot_hashes_json_stores(tmp_path):
    """hallways.json-class files carry a content hash, so a similar-size
    overwrite changes the snapshot signature (PR #46 review)."""
    import hashlib

    store = tmp_path / "store"
    store.mkdir()
    f = store / "hallways.json"
    f.write_text('{"a": 1}', encoding="utf-8")
    snap = _snapshot_sage_tree(store)
    assert snap is not None
    kind, digest, _ = snap["hallways.json"]
    assert kind == "hash"
    assert digest == hashlib.sha256(b'{"a": 1}').hexdigest()
    # Same-size different content → different signature.
    f.write_text('{"a": 2}', encoding="utf-8")
    snap2 = _snapshot_sage_tree(store)
    assert snap2 is not None and snap2["hallways.json"][1] != digest
