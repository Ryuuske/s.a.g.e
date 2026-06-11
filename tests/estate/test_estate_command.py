"""Tests for sage.estate.command — the ``sage estate --json`` CLI (Phase 3).

Covers the ADR-0003 standalone-subprocess read contract:
1. Pre-check: no chroma.sqlite3 → graceful degradation (no client constructed).
2. Copy-snapshot: the COPY is opened, never the live path; temp is cleaned up.
3. Guarded open returning None → graceful degradation.
4. Success: a mocked snapshot collection → schema-valid estate model JSON.
5. cmd_estate exit codes: 0 on success / degradation, 1 on build (schema) drift.
6. End-to-end wiring smoke (subprocess): ``sage estate --json`` dispatches.

No live ~/.sage is touched: the snapshot source is a synthetic temp dir, the
collection open is mocked, and the slot ledger + revision counter are redirected
to tmp paths.

WHERE: tests/estate/test_estate_command.py
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys
import types

import pytest

from sage_mcp.estate import command as cmd_mod
from sage_mcp.estate.adapter import estate_model as em

_HERE = pathlib.Path(__file__).resolve().parent
_AGENTS_FIXTURES = _HERE / "fixtures" / "workshop" / "agents"

_METADATA_ROWS = [
    {"wing": "dev", "room": "main", "hall": "handoff", "strength": 0.9},
    {"wing": "project", "room": "planning", "hall": "plans", "strength": 0.7},
    {"wing": "xyzzy-unknown-wing", "room": "misc", "hall": "facts", "strength": 0.3},
    {"wing": "?", "room": "?", "hall": "", "strength": 0.2},
]


class _FakeCol:
    """Minimal Chroma-collection stand-in: count() + paginated metadata get()."""

    def __init__(self, rows):
        self._rows = rows

    def count(self):
        return len(self._rows)

    def get(self, include=None, limit=1000, offset=0, where=None):
        assert include == ["metadatas"], "CLI must request metadata only — no bodies"
        return {"metadatas": self._rows[offset : offset + limit]}


def _make_nook_dir(tmp_path) -> str:
    """Create a synthetic nook dir with a chroma.sqlite3 marker (no real store)."""
    nook = tmp_path / "nook"
    nook.mkdir()
    (nook / "chroma.sqlite3").write_text("synthetic", encoding="utf-8")
    (nook / "seg").mkdir()
    (nook / "seg" / "data_level0.bin").write_text("x", encoding="utf-8")
    return str(nook)


def _isolate_model_state(monkeypatch, tmp_path):
    """Redirect the slot ledger + revision counter so no real ~/.sage is touched."""
    ledger_path = tmp_path / "ledger.json"
    monkeypatch.setattr(em, "_REVISION_PATH", tmp_path / "rev.json")
    real_build = em.build_estate_model

    def _build_isolated(metadata_rows, wing_config, **kwargs):
        kwargs.setdefault("ledger_path", ledger_path)
        kwargs.setdefault("agents_dir", _AGENTS_FIXTURES)
        return real_build(metadata_rows, wing_config, **kwargs)

    monkeypatch.setattr(em, "build_estate_model", _build_isolated)
    monkeypatch.setattr(cmd_mod, "_resolve_wing_config", lambda: {"version": 1, "wings": {}})
    import sage_mcp.telemetry as _tel

    monkeypatch.setattr(_tel, "read_recent", lambda limit=50: [])


# ── 1. Pre-check: no chroma.sqlite3 ──────────────────────────────────────────


def test_snapshot_no_chroma_sqlite_degrades(tmp_path):
    """A nook dir without chroma.sqlite3 degrades, never constructs a client."""
    empty_nook = tmp_path / "nook"
    empty_nook.mkdir()
    result = cmd_mod.build_estate_model_via_snapshot(str(empty_nook), "nook_drawers")
    assert result["available"] is False
    assert "chroma.sqlite3" in result["reason"]


# ── 2. Copy-snapshot opens the COPY, not the live path; cleans up ────────────


def test_snapshot_opens_copy_not_live_and_cleans_up(monkeypatch, tmp_path):
    """_open is called on a COPY under a temp dir (never the live nook), and the
    snapshot is removed afterward (ADR-0003)."""
    nook_path = _make_nook_dir(tmp_path)
    snapshot_parent = tmp_path / "snaps"
    snapshot_parent.mkdir()

    opened_paths = []

    def _spy_open(path, *, collection_name=None, out=None):
        opened_paths.append(path)
        return None  # force degradation after recording the path

    monkeypatch.setattr("sage_mcp.nook._open_collection_or_explain", _spy_open)

    result = cmd_mod.build_estate_model_via_snapshot(
        nook_path, "nook_drawers", snapshot_root=str(snapshot_parent), max_open_retries=1
    )

    assert result["available"] is False  # _open returned None
    assert len(opened_paths) == 1
    opened = opened_paths[0]
    assert opened != nook_path, "must open the COPY, never the live path"
    assert str(snapshot_parent) in opened, "copy must live under the temp snapshot root"
    # Snapshot cleaned up: the temp parent has no leftover children.
    assert list(snapshot_parent.iterdir()) == [], "snapshot temp dir was not cleaned up"


def test_snapshot_retries_then_degrades_to_busy(monkeypatch, tmp_path):
    """A persistently-unopenable copy (mid-write) retries N times, takes a fresh
    snapshot each time, then degrades to a path-free 'store busy' envelope
    (Codex fold: snapshot consistency without disrupting writers)."""
    nook_path = _make_nook_dir(tmp_path)
    snapshot_parent = tmp_path / "snaps"
    snapshot_parent.mkdir()
    opened = []
    monkeypatch.setattr(
        "sage_mcp.nook._open_collection_or_explain",
        lambda path, *, collection_name=None, out=None: opened.append(path) or None,
    )

    result = cmd_mod.build_estate_model_via_snapshot(
        nook_path,
        "nook_drawers",
        snapshot_root=str(snapshot_parent),
        max_open_retries=3,
        retry_backoff_s=0,
    )

    assert result["available"] is False
    assert "busy" in result["reason"]
    assert "/home/" not in result["reason"]  # reason is path-free / redacted
    assert len(opened) == 3, "should retry the copy max_open_retries times"
    # Every attempt opened a fresh COPY, never the live path.
    assert all(p != nook_path for p in opened)
    # All snapshots cleaned up.
    assert list(snapshot_parent.iterdir()) == [], "snapshots leaked across retries"


# ── 3. Success path → schema-valid model ─────────────────────────────────────


def test_snapshot_success_builds_valid_model(monkeypatch, tmp_path):
    """A mocked snapshot collection yields a schema-valid estate model."""
    _isolate_model_state(monkeypatch, tmp_path)
    nook_path = _make_nook_dir(tmp_path)
    fake_col = _FakeCol(_METADATA_ROWS)
    monkeypatch.setattr(
        "sage_mcp.nook._open_collection_or_explain",
        lambda path, *, collection_name=None, out=None: fake_col,
    )

    model = cmd_mod.build_estate_model_via_snapshot(nook_path, "nook_drawers")

    assert "available" not in model
    assert model["version"] == "1.0"
    em.validate_estate_model(model)  # raises on drift
    nook = next(b for b in model["buildings"] if b["id"] == "nook")
    wt = {w["id"]: w["type"] for w in nook["wings"]}
    assert wt.get("wing:dev") == "dev"  # "dev" is a known type key even w/ empty config
    assert wt.get("wing:xyzzy-unknown-wing") == "unknown"  # unregistered → unknown
    assert "wing:?" in wt


# ── 4. cmd_estate exit codes + output ────────────────────────────────────────


def test_cmd_estate_prints_json_on_success(monkeypatch, tmp_path, capsys):
    """cmd_estate prints parseable JSON to stdout and exits cleanly."""
    _isolate_model_state(monkeypatch, tmp_path)
    nook_path = _make_nook_dir(tmp_path)
    fake_col = _FakeCol(_METADATA_ROWS)
    monkeypatch.setattr(
        "sage_mcp.nook._open_collection_or_explain",
        lambda path, *, collection_name=None, out=None: fake_col,
    )

    cfg = types.SimpleNamespace(nook_path=nook_path, collection_name="nook_drawers")
    monkeypatch.setattr("sage_mcp.config.SageConfig", lambda: cfg)

    cmd_mod.cmd_estate(argparse.Namespace(nook=None, json=True))
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["version"] == "1.0"


def test_cmd_estate_degradation_exits_zero(monkeypatch, tmp_path, capsys):
    """A no-nook degradation prints the envelope and exits 0 (valid answer)."""
    empty_nook = tmp_path / "nook"
    empty_nook.mkdir()

    cfg = types.SimpleNamespace(nook_path=str(empty_nook), collection_name="nook_drawers")
    monkeypatch.setattr("sage_mcp.config.SageConfig", lambda: cfg)

    cmd_mod.cmd_estate(argparse.Namespace(nook=None, json=True))  # no SystemExit
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["available"] is False


def test_cmd_estate_build_failure_exits_nonzero(monkeypatch, tmp_path):
    """A build/schema-drift failure maps to a non-zero exit (plan 3.4)."""
    nook_path = _make_nook_dir(tmp_path)

    def _boom(*a, **kw):
        raise RuntimeError("schema drift")

    monkeypatch.setattr(cmd_mod, "build_estate_model_via_snapshot", _boom)

    cfg = types.SimpleNamespace(nook_path=nook_path, collection_name="nook_drawers")
    monkeypatch.setattr("sage_mcp.config.SageConfig", lambda: cfg)

    with pytest.raises(SystemExit) as exc:
        cmd_mod.cmd_estate(argparse.Namespace(nook=None, json=True))
    assert exc.value.code == 1


def test_cmd_estate_build_failure_reason_is_redacted(monkeypatch, tmp_path, capsys):
    """A build-failure exc carrying a leaked value/home path is REDACTED before
    printing to stderr — parity with the MCP path (PR #34 review)."""
    nook_path = _make_nook_dir(tmp_path)

    def _boom(*a, **kw):
        raise RuntimeError(
            "validation failed on /home/secretuser/.sage/nook value ghp_aaaaaaaaaaaaaaaaaaaa"
        )

    monkeypatch.setattr(cmd_mod, "build_estate_model_via_snapshot", _boom)
    cfg = types.SimpleNamespace(nook_path=nook_path, collection_name="nook_drawers")
    monkeypatch.setattr("sage_mcp.config.SageConfig", lambda: cfg)

    with pytest.raises(SystemExit):
        cmd_mod.cmd_estate(argparse.Namespace(nook=None, json=True))
    err = capsys.readouterr().err
    assert "/home/" not in err, f"home path leaked to stderr: {err!r}"
    assert "ghp_aaaaaaaaaaaaaaaaaaaa" not in err, "secret token leaked to stderr"


# ── 4b. Pagination + None-row + cleanup-on-failure (test F1/F4/F6 fold) ──────


def test_fetch_metadata_multi_batch_pagination():
    """_fetch_metadata_no_bodies pages through >1000 rows across batches."""
    rows = [{"wing": "dev", "room": "main", "hall": "handoff"} for _ in range(2500)]
    col = _FakeCol(rows)
    fetched = cmd_mod._fetch_metadata_no_bodies(col)
    assert len(fetched) == 2500


def test_fetch_metadata_coerces_none_rows():
    """ChromaDB can return None metadata entries — they coerce to {} (no crash)."""

    class _NoneRowCol:
        def count(self):
            return 3

        def get(self, include=None, limit=1000, offset=0, where=None):
            assert include == ["metadatas"]
            return {
                "metadatas": [None, {"wing": "dev", "room": "main"}, None][offset : offset + limit]
            }

    fetched = cmd_mod._fetch_metadata_no_bodies(_NoneRowCol())
    assert len(fetched) == 3
    assert all(isinstance(r, dict) for r in fetched)


def test_copytree_failure_degrades_to_busy_not_raise(monkeypatch, tmp_path):
    """A copytree failure under a live write degrades to a path-free 'store busy'
    envelope (NOT a raised exception), and every temp dir is cleaned up
    (Codex fold round 3 — copytree itself can raise mid-traversal)."""
    nook_path = _make_nook_dir(tmp_path)
    snapshot_parent = tmp_path / "snaps"
    snapshot_parent.mkdir()
    calls = {"n": 0}
    real_copytree = __import__("shutil").copytree

    def _boom_copytree(src, dst, *a, **kw):
        # Only the snapshot copy (src == the live nook) fails; delegate any
        # unrelated internal copytree to the real implementation.
        if os.path.realpath(src) == os.path.realpath(nook_path):
            calls["n"] += 1
            raise FileNotFoundError("/home/secretuser/.sage/nook/seg/data_level0.bin vanished")
        return real_copytree(src, dst, *a, **kw)

    monkeypatch.setattr("shutil.copytree", _boom_copytree)

    result = cmd_mod.build_estate_model_via_snapshot(
        nook_path,
        "nook_drawers",
        snapshot_root=str(snapshot_parent),
        max_open_retries=3,
        retry_backoff_s=0,
    )
    assert result["available"] is False
    assert "busy" in result["reason"]
    assert "/home/" not in result["reason"], "copytree exc text leaked a home path"
    assert calls["n"] == 3, "copytree failure should be retried, not raised"
    assert list(snapshot_parent.iterdir()) == [], "snapshot temp dirs leaked on copytree failure"


def test_copytree_recovers_after_transient_failures(monkeypatch, tmp_path):
    """copytree failing the first N-1 attempts then succeeding yields a model
    (Codex fold round 3 regression test)."""
    _isolate_model_state(monkeypatch, tmp_path)
    nook_path = _make_nook_dir(tmp_path)
    snapshot_parent = tmp_path / "snaps"
    snapshot_parent.mkdir()

    real_copytree = __import__("shutil").copytree
    state = {"n": 0}

    def _flaky_copytree(src, dst, *a, **kw):
        # Only count/fail the snapshot copy (src == the live nook); delegate any
        # unrelated internal copytree to the real implementation.
        if os.path.realpath(src) == os.path.realpath(nook_path):
            state["n"] += 1
            if state["n"] < 3:
                raise OSError("transient mid-write copy error")
        return real_copytree(src, dst, *a, **kw)

    monkeypatch.setattr("shutil.copytree", _flaky_copytree)
    monkeypatch.setattr(
        "sage_mcp.nook._open_collection_or_explain",
        lambda path, *, collection_name=None, out=None: _FakeCol(_METADATA_ROWS),
    )

    model = cmd_mod.build_estate_model_via_snapshot(
        nook_path,
        "nook_drawers",
        snapshot_root=str(snapshot_parent),
        max_open_retries=5,
        retry_backoff_s=0,
    )
    assert model["version"] == "1.0", "should recover once copytree succeeds"
    assert state["n"] == 3
    assert list(snapshot_parent.iterdir()) == [], "snapshots leaked across retries"


def test_retry_honors_backoff_between_failed_attempts(monkeypatch, tmp_path):
    """Failed attempts back off before retrying — the configured delay is on the
    actual retry path, not dead code after a `continue` (Codex fold round 4)."""
    nook_path = _make_nook_dir(tmp_path)
    snapshot_parent = tmp_path / "snaps"
    snapshot_parent.mkdir()
    # Every open returns None → all attempts fail → degrade to busy.
    monkeypatch.setattr(
        "sage_mcp.nook._open_collection_or_explain",
        lambda path, *, collection_name=None, out=None: None,
    )
    sleeps: list[float] = []
    monkeypatch.setattr("sage_mcp.estate.command.time.sleep", lambda s: sleeps.append(s))

    result = cmd_mod.build_estate_model_via_snapshot(
        nook_path,
        "nook_drawers",
        snapshot_root=str(snapshot_parent),
        max_open_retries=3,
        retry_backoff_s=0.25,
    )
    assert result["available"] is False
    # 3 attempts → backoff between them = 2 sleeps, each the configured delay.
    assert sleeps == [0.25, 0.25], f"backoff not honored on retry path: {sleeps}"


# ── 4c. Live tunnels (Fix B) + metadata-read retry (Fix D) — PR #34 review ───


def test_cli_populates_tunnels_from_live_json(monkeypatch, tmp_path):
    """The CLI reads the live tunnels.json (a sibling of the nook dir, NOT in the
    snapshot) so cross-wing passages aren't lost (PR #34 review)."""
    _isolate_model_state(monkeypatch, tmp_path)
    nook_path = _make_nook_dir(tmp_path)
    # tunnels.json is a SIBLING of the nook dir.
    (tmp_path / "tunnels.json").write_text(
        json.dumps(
            [
                {
                    "id": "tnl-1",
                    "label": "shared",
                    "source": {"wing": "dev", "room": "main"},
                    "target": {"wing": "project", "room": "planning"},
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sage_mcp.nook._open_collection_or_explain",
        lambda path, *, collection_name=None, out=None: _FakeCol(_METADATA_ROWS),
    )
    model = cmd_mod.build_estate_model_via_snapshot(nook_path, "nook_drawers")
    nook = next(b for b in model["buildings"] if b["id"] == "nook")
    assert len(nook["tunnels"]) == 1
    assert nook["tunnels"][0]["endpoints"] == ["wing:dev", "wing:project"]


def test_read_live_tunnels_missing_file_returns_empty(tmp_path):
    """No tunnels.json → []."""
    nook = tmp_path / "nook"
    nook.mkdir()
    assert cmd_mod._read_live_tunnels(str(nook)) == []


def test_read_kg_stats_reads_counts_readonly(tmp_path):
    """_read_kg_stats reads entity/triple counts; an absent KG → (0,0) and is
    NEVER created (read-only URI mode)."""
    import sqlite3

    missing = str(tmp_path / "nope.sqlite3")
    assert cmd_mod._read_kg_stats(missing) == (0, 0)
    assert not os.path.exists(missing), "_read_kg_stats created an absent KG file"

    kg = str(tmp_path / "knowledge_graph.sqlite3")
    con = sqlite3.connect(kg)
    con.execute("CREATE TABLE entities (id INTEGER)")
    con.execute("CREATE TABLE triples (id INTEGER)")
    con.executemany("INSERT INTO entities VALUES (?)", [(i,) for i in range(3)])
    con.executemany("INSERT INTO triples VALUES (?)", [(i,) for i in range(5)])
    con.commit()
    con.close()
    assert cmd_mod._read_kg_stats(kg) == (3, 5)


def test_cli_populates_kg_from_snapshot(monkeypatch, tmp_path):
    """The CLI reads KG counts from the snapshot's knowledge_graph.sqlite3 (inside
    the nook → captured by copytree) instead of emitting 0/0 (PR #34 review)."""
    import sqlite3

    _isolate_model_state(monkeypatch, tmp_path)
    nook_path = _make_nook_dir(tmp_path)
    kg = os.path.join(nook_path, "knowledge_graph.sqlite3")
    con = sqlite3.connect(kg)
    con.execute("CREATE TABLE entities (id INTEGER)")
    con.execute("CREATE TABLE triples (id INTEGER)")
    con.executemany("INSERT INTO entities VALUES (?)", [(i,) for i in range(4)])
    con.executemany("INSERT INTO triples VALUES (?)", [(i,) for i in range(9)])
    con.commit()
    con.close()
    monkeypatch.setattr(
        "sage_mcp.nook._open_collection_or_explain",
        lambda path, *, collection_name=None, out=None: _FakeCol(_METADATA_ROWS),
    )
    model = cmd_mod.build_estate_model_via_snapshot(nook_path, "nook_drawers")
    nook = next(b for b in model["buildings"] if b["id"] == "nook")
    assert nook["kg"] == {"entities": 4, "relations": 9}


def test_cli_reads_kg_from_explicit_default_location(monkeypatch, tmp_path):
    """The default nook's KG lives OUTSIDE the nook (~/.sage/knowledge_graph.sqlite3),
    so the CLI reads the explicit kg_path, NOT the snapshot (PR #34 review)."""
    import sqlite3

    _isolate_model_state(monkeypatch, tmp_path)
    nook_path = _make_nook_dir(tmp_path)
    # KG sits beside the nook (the default-location case), NOT inside it.
    kg = str(tmp_path / "knowledge_graph.sqlite3")
    con = sqlite3.connect(kg)
    con.execute("CREATE TABLE entities (id INTEGER)")
    con.execute("CREATE TABLE triples (id INTEGER)")
    con.executemany("INSERT INTO entities VALUES (?)", [(i,) for i in range(6)])
    con.executemany("INSERT INTO triples VALUES (?)", [(i,) for i in range(2)])
    con.commit()
    con.close()
    monkeypatch.setattr(
        "sage_mcp.nook._open_collection_or_explain",
        lambda path, *, collection_name=None, out=None: _FakeCol(_METADATA_ROWS),
    )
    model = cmd_mod.build_estate_model_via_snapshot(nook_path, "nook_drawers", kg_path=kg)
    nook = next(b for b in model["buildings"] if b["id"] == "nook")
    assert nook["kg"] == {"entities": 6, "relations": 2}


def test_metadata_read_failure_retries_then_busy(monkeypatch, tmp_path):
    """A copy that OPENS but fails on count()/get() is retried and degrades to a
    'store busy' envelope — not exit 1 / schema drift (PR #34 review)."""
    nook_path = _make_nook_dir(tmp_path)
    snapshot_parent = tmp_path / "snaps"
    snapshot_parent.mkdir()

    class _TornCol:
        def count(self):
            raise RuntimeError("database disk image is malformed")

        def get(self, **kw):
            raise RuntimeError("unreachable")

    opened = {"n": 0}

    def _open(path, *, collection_name=None, out=None):
        opened["n"] += 1
        return _TornCol()

    monkeypatch.setattr("sage_mcp.nook._open_collection_or_explain", _open)
    result = cmd_mod.build_estate_model_via_snapshot(
        nook_path,
        "nook_drawers",
        snapshot_root=str(snapshot_parent),
        max_open_retries=3,
        retry_backoff_s=0,
    )
    assert result["available"] is False
    assert "busy" in result["reason"]
    assert opened["n"] == 3, "metadata-read failure must be retried, not raised"
    assert list(snapshot_parent.iterdir()) == [], "snapshots leaked on read failure"


def test_snapshot_closes_chroma_handle_before_rmtree(monkeypatch, tmp_path):
    """The snapshot's Chroma client is closed before the temp dir is removed, so
    Windows doesn't leak snapshot dirs behind held file locks (PR #34 review)."""
    _isolate_model_state(monkeypatch, tmp_path)
    nook_path = _make_nook_dir(tmp_path)
    monkeypatch.setattr(
        "sage_mcp.nook._open_collection_or_explain",
        lambda path, *, collection_name=None, out=None: _FakeCol(_METADATA_ROWS),
    )
    closed: list[str] = []
    monkeypatch.setattr(
        "sage_mcp.repair._close_chroma_handles", lambda p, backend=None: closed.append(p)
    )

    model = cmd_mod.build_estate_model_via_snapshot(nook_path, "nook_drawers")
    assert model["version"] == "1.0"
    assert closed, "snapshot Chroma handle was not closed before cleanup"
    assert closed[0].endswith("nook"), "closed the snapshot nook path"


def test_cli_vector_disabled_degrades_without_opening(monkeypatch, tmp_path):
    """A divergent nook (#1222) degrades WITHOUT opening Chroma on the copy
    (which can segfault) — parity with the MCP guard (PR #34 review)."""
    nook_path = _make_nook_dir(tmp_path)
    monkeypatch.setattr(cmd_mod, "_snapshot_vector_disabled", lambda *a, **kw: True)
    opened = {"n": 0}

    def _open(*a, **kw):
        opened["n"] += 1
        return object()

    monkeypatch.setattr("sage_mcp.nook._open_collection_or_explain", _open)
    result = cmd_mod.build_estate_model_via_snapshot(nook_path, "nook_drawers")
    assert result["available"] is False
    assert "vector index" in result["reason"]
    assert opened["n"] == 0, "must NOT open Chroma on a divergent copy"


# ── 5. End-to-end wiring smoke (subprocess) ──────────────────────────────────


@pytest.mark.integration
def test_sage_estate_cli_wiring(tmp_path):
    """`sage estate --json` dispatches end-to-end and degrades on a missing nook.

    Points SAGE_NOOK_PATH at a nonexistent dir so no real store is touched; the
    command must print an ``available:false`` envelope and exit 0.
    """
    env = os.environ.copy()
    env["SAGE_NOOK_PATH"] = str(tmp_path / "no-such-nook")
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-m", "sage_mcp", "estate", "--json"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    diag = f"rc={result.returncode}; stdout={result.stdout!r}; stderr={result.stderr!r}"
    assert result.returncode == 0, f"estate CLI failed: {diag}"
    parsed = json.loads(result.stdout)
    assert parsed["available"] is False, diag


@pytest.mark.integration
def test_sage_estate_cli_accepts_nook_flag_after_subcommand(tmp_path):
    """The documented `sage estate --json --nook <path>` form parses (PR #34
    review: --nook was only on the global parser, rejected after the subcommand)."""
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("SAGE_NOOK_PATH", None)
    result = subprocess.run(
        [sys.executable, "-m", "sage_mcp", "estate", "--json", "--nook", str(tmp_path / "no-nook")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    diag = f"rc={result.returncode}; stdout={result.stdout!r}; stderr={result.stderr!r}"
    assert result.returncode == 0, f"--nook not accepted after subcommand: {diag}"
    assert json.loads(result.stdout)["available"] is False, diag


@pytest.mark.integration
def test_sage_estate_cli_accepts_wing_and_no_bodies_flags(tmp_path):
    """The documented `sage estate --json --wing <id> --no-bodies` form parses
    (PR #34 review: only --json/--level/--nook were registered)."""
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["SAGE_NOOK_PATH"] = str(tmp_path / "no-nook")
    result = subprocess.run(
        [sys.executable, "-m", "sage_mcp", "estate", "--json", "--wing", "dev", "--no-bodies"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    diag = f"rc={result.returncode}; stdout={result.stdout!r}; stderr={result.stderr!r}"
    assert result.returncode == 0, f"--wing/--no-bodies not accepted: {diag}"
    assert json.loads(result.stdout)["available"] is False, diag


@pytest.mark.integration
def test_sage_estate_cli_accepts_level_flag(tmp_path):
    """The documented `sage estate --json --level N` invocation parses (PR #34
    review: argparse previously rejected --level)."""
    env = os.environ.copy()
    env["SAGE_NOOK_PATH"] = str(tmp_path / "no-such-nook")
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-m", "sage_mcp", "estate", "--json", "--level", "2"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    diag = f"rc={result.returncode}; stdout={result.stdout!r}; stderr={result.stderr!r}"
    assert result.returncode == 0, f"--level not accepted: {diag}"
    assert json.loads(result.stdout)["available"] is False, diag
