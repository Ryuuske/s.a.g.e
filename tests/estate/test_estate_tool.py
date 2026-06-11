"""Tests for sage_mcp.mcp_server.tool_estate — the nook_estate MCP handler (Phase 3).

Covers the ADR-0003 read-path contract at the MCP surface:
1. Graceful degradation: no nook → ``{"available": False, "reason": ...}``.
2. Metadata-fetch failure → graceful ``{"available": False}`` (no crash).
3. Success path: a mocked in-process collection + synthetic metadata rows
   produces a schema-valid estate model with nook + workshop buildings.
4. The ``level`` argument is clamped to 0-3.

No live store is touched: ``_get_collection`` and ``_get_cached_metadata`` are
mocked, the slot ledger is redirected to a tmp path, and the KG/tunnel/telemetry
side-reads are stubbed.

WHERE: tests/estate/test_estate_tool.py
"""

import json
import pathlib
import types
from unittest.mock import patch

from sage_mcp import mcp_server
from sage_mcp.estate.adapter import estate_model as em

_HERE = pathlib.Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
_SCHEMA_PATH = _ROOT / "docs" / "projects" / "sage-estate-dashboard" / "estate-model.schema.json"
_SCHEMA = json.loads(_SCHEMA_PATH.read_text())
_AGENTS_FIXTURES = _HERE / "fixtures" / "workshop" / "agents"

_METADATA_ROWS = [
    {"wing": "dev", "room": "main", "hall": "handoff", "strength": 0.9},
    {"wing": "project", "room": "planning", "hall": "plans", "strength": 0.7},
    {"wing": "xyzzy-unknown-wing", "room": "misc", "hall": "facts", "strength": 0.3},
    {"wing": "?", "room": "?", "hall": "", "strength": 0.2},
]


def _init_nook(monkeypatch, tmp_path):
    """Point _config.nook_path at a tmp nook with a chroma.sqlite3 marker so the
    read-only existence precheck passes and execution reaches _get_collection
    (which the tests mock). No real store is touched."""
    nook = tmp_path / "nook"
    nook.mkdir(exist_ok=True)
    (nook / "chroma.sqlite3").write_text("synthetic", encoding="utf-8")
    monkeypatch.setattr(mcp_server, "_config", types.SimpleNamespace(nook_path=str(nook)))
    # A REAL read-only KG sqlite (7 entities, 12 triples) at the resolved path,
    # so the shared read_counts_readonly helper returns real counts WITHOUT
    # constructing a (mutating) KnowledgeGraph. Tests that exercise the absent-KG
    # read-only contract create their own nook without this.
    import sqlite3 as _sqlite3

    _kg = nook / "knowledge_graph.sqlite3"
    _con = _sqlite3.connect(str(_kg))
    _con.execute("CREATE TABLE entities (id INTEGER)")
    _con.execute("CREATE TABLE triples (id INTEGER)")
    _con.executemany("INSERT INTO entities VALUES (?)", [(i,) for i in range(7)])
    _con.executemany("INSERT INTO triples VALUES (?)", [(i,) for i in range(12)])
    _con.commit()
    _con.close()
    monkeypatch.setattr(mcp_server, "_resolve_kg_path", lambda: str(_kg))
    # Neutralize the vector-divergence probe — the synthetic chroma.sqlite3 is a
    # text marker, not a real store, so the real probe would mis-fire. Tests that
    # exercise the disabled path re-set _vector_disabled after calling this.
    monkeypatch.setattr(mcp_server, "_refresh_vector_disabled_flag", lambda: None)
    monkeypatch.setattr(mcp_server, "_vector_disabled", False)


# ── 1. Graceful degradation: no nook ─────────────────────────────────────────


def test_estate_no_nook_returns_unavailable():
    """No collection → ``{"available": False}`` with a reason, never a crash."""
    with patch.object(mcp_server, "_get_collection", return_value=None):
        result = mcp_server.tool_estate()
    assert result["available"] is False
    assert "reason" in result


def test_estate_absent_kg_is_not_created(monkeypatch, tmp_path):
    """nook_estate must NOT create knowledge_graph.sqlite3 when absent — calling
    tool_kg_stats would (mkdir + CREATE TABLE), mutating ~/.sage (PR #34 review)."""
    nook = tmp_path / "nook"
    nook.mkdir()
    (nook / "chroma.sqlite3").write_text("synthetic", encoding="utf-8")
    monkeypatch.setattr(mcp_server, "_config", types.SimpleNamespace(nook_path=str(nook)))
    monkeypatch.setattr(mcp_server, "_refresh_vector_disabled_flag", lambda: None)
    monkeypatch.setattr(mcp_server, "_vector_disabled", False)
    monkeypatch.setattr(
        mcp_server, "_resolve_kg_path", lambda: str(nook / "knowledge_graph.sqlite3")
    )
    kg_called = {"n": 0}
    monkeypatch.setattr(
        mcp_server, "tool_kg_stats", lambda: kg_called.__setitem__("n", kg_called["n"] + 1)
    )
    monkeypatch.setattr(mcp_server, "tool_list_tunnels", lambda *a, **kw: [])
    import sage_mcp.telemetry as _tel

    monkeypatch.setattr(_tel, "read_recent", lambda limit=50: [])
    # Isolate the model state so the build itself touches no real ~/.sage.
    monkeypatch.setattr(em, "_REVISION_PATH", tmp_path / "rev.json")
    real_build = em.build_estate_model
    monkeypatch.setattr(
        em,
        "build_estate_model",
        lambda rows, wc, **kw: real_build(
            rows, wc, ledger_path=tmp_path / "l.json", agents_dir=_AGENTS_FIXTURES, **kw
        ),
    )
    with patch.object(mcp_server, "_get_collection", return_value=object()):
        with patch.object(mcp_server, "_get_cached_metadata", return_value=_METADATA_ROWS):
            result = mcp_server.tool_estate()

    assert kg_called["n"] == 0, "tool_kg_stats must not be called when the KG file is absent"
    assert not (nook / "knowledge_graph.sqlite3").exists(), "estate read created the KG!"
    nook_b = next(b for b in result["buildings"] if b["id"] == "nook")
    assert nook_b["kg"] == {"entities": 0, "relations": 0}


def test_estate_kg_read_is_readonly_never_constructs_graph(monkeypatch, tmp_path):
    """nook_estate reads KG counts WITHOUT constructing a (mutating)
    KnowledgeGraph — its constructor mkdir/chmod/_init_db/WAL/migrations write
    even an EXISTING KG, violating ADR-0003 (PR #34 ultra-review)."""
    import sage_mcp.knowledge_graph as kg_mod

    _init_nook(monkeypatch, tmp_path)  # seeds a real KG with 7 entities, 12 triples

    def _boom(*a, **kw):
        raise AssertionError("KnowledgeGraph constructed during a read-only estate call")

    monkeypatch.setattr(kg_mod, "KnowledgeGraph", _boom)
    monkeypatch.setattr(mcp_server, "tool_list_tunnels", lambda *a, **kw: [])
    import sage_mcp.telemetry as _tel

    monkeypatch.setattr(_tel, "read_recent", lambda limit=50: [])
    monkeypatch.setattr(em, "_REVISION_PATH", tmp_path / "rev.json")
    real_build = em.build_estate_model
    monkeypatch.setattr(
        em,
        "build_estate_model",
        lambda rows, wc, **kw: real_build(
            rows, wc, ledger_path=tmp_path / "l.json", agents_dir=_AGENTS_FIXTURES, **kw
        ),
    )
    with patch.object(mcp_server, "_get_collection", return_value=object()):
        with patch.object(mcp_server, "_get_cached_metadata", return_value=_METADATA_ROWS):
            result = mcp_server.tool_estate()  # must not raise the AssertionError

    nook = next(b for b in result["buildings"] if b["id"] == "nook")
    assert nook["kg"] == {"entities": 7, "relations": 12}, "read-only KG counts must flow through"


def test_estate_vector_disabled_degrades_without_opening(monkeypatch, tmp_path):
    """When the vector index has diverged (#1222), tool_estate degrades WITHOUT
    opening Chroma (opening can segfault) — parity with tool_status (PR #34)."""
    _init_nook(monkeypatch, tmp_path)
    monkeypatch.setattr(mcp_server, "_refresh_vector_disabled_flag", lambda: None)
    monkeypatch.setattr(mcp_server, "_vector_disabled", True)
    called = {"n": 0}
    monkeypatch.setattr(
        mcp_server, "_get_collection", lambda *a, **kw: called.__setitem__("n", called["n"] + 1)
    )
    result = mcp_server.tool_estate()
    assert result["available"] is False
    assert "vector index" in result["reason"]
    assert called["n"] == 0, "must NOT open Chroma when vectors are disabled"


def test_estate_absent_nook_does_not_create_store(monkeypatch, tmp_path):
    """Read-only contract (PR #34 review): an absent nook degrades WITHOUT ever
    constructing a client — no chroma.sqlite3 is created, _get_collection is not
    even called (the existence precheck short-circuits first)."""
    empty_nook = tmp_path / "nook"
    empty_nook.mkdir()  # dir exists, but no chroma.sqlite3
    monkeypatch.setattr(mcp_server, "_config", types.SimpleNamespace(nook_path=str(empty_nook)))
    called = {"n": 0}

    def _spy(*a, **kw):
        called["n"] += 1
        return None

    monkeypatch.setattr(mcp_server, "_get_collection", _spy)
    result = mcp_server.tool_estate()

    assert result["available"] is False
    assert called["n"] == 0, "_get_collection must NOT be called on an absent nook"
    assert not (empty_nook / "chroma.sqlite3").exists(), "read path created the store!"
    assert list(empty_nook.iterdir()) == [], "read path wrote into the nook dir"


# ── 2. Metadata-fetch failure degrades gracefully ────────────────────────────


def test_estate_metadata_failure_returns_unavailable(monkeypatch, tmp_path):
    """A metadata-fetch exception degrades to ``available: False`` (no raise)."""
    _init_nook(monkeypatch, tmp_path)
    fake_col = object()
    with patch.object(mcp_server, "_get_collection", return_value=fake_col):
        with patch.object(
            mcp_server, "_get_cached_metadata", side_effect=RuntimeError("store busy")
        ):
            result = mcp_server.tool_estate()
    assert result["available"] is False
    assert "store busy" in result["reason"]


# ── 3. Success path: mocked collection → schema-valid model ──────────────────


def _patch_external_reads(monkeypatch, tmp_path):
    """Stub every side-read so no live store/kg/telemetry is touched.

    Redirects the slot ledger to *tmp_path* and forces the workshop adapter to
    read the committed test fixtures rather than the real ``~/.claude/agents``.
    """
    ledger_path = tmp_path / "ledger.json"
    rev_path = tmp_path / "_estate_revision.json"

    real_build = em.build_estate_model

    def _build_isolated(metadata_rows, wing_config, **kwargs):
        kwargs.setdefault("ledger_path", ledger_path)
        kwargs.setdefault("agents_dir", _AGENTS_FIXTURES)
        return real_build(metadata_rows, wing_config, **kwargs)

    _init_nook(monkeypatch, tmp_path)
    monkeypatch.setattr(em, "build_estate_model", _build_isolated)
    monkeypatch.setattr(em, "_REVISION_PATH", rev_path)
    # KG success branch: real KnowledgeGraph.stats() keys are entities/triples
    # (NOT entity_count/triple_count — the mock-fidelity miss PR #34 caught).
    monkeypatch.setattr(mcp_server, "tool_kg_stats", lambda: {"entities": 7, "triples": 12})
    # Tunnel processing branch: tool_list_tunnels returns a LIST of records with
    # NESTED source/target (the real nook_graph.list_tunnels shape). One
    # well-formed cross-wing link + two malformed entries that must be skipped.
    monkeypatch.setattr(
        mcp_server,
        "tool_list_tunnels",
        lambda *a, **kw: [
            {
                "id": "t1",
                "label": "shared-ref",
                "source": {"wing": "dev", "room": "main"},
                "target": {"wing": "project", "room": "planning"},
            },
            "not-a-dict",  # skipped by the isinstance guard
            {"source": {"wing": ""}, "target": {"wing": ""}},  # skipped by the src/tgt guard
        ],
    )
    # Telemetry: isolate from the real turns.jsonl.
    import sage_mcp.telemetry as _tel

    monkeypatch.setattr(_tel, "read_recent", lambda limit=50: [])


def test_estate_success_path_builds_valid_model(monkeypatch, tmp_path):
    """A mocked in-process collection yields a schema-valid estate model."""
    _patch_external_reads(monkeypatch, tmp_path)
    fake_col = object()
    with patch.object(mcp_server, "_get_collection", return_value=fake_col):
        with patch.object(mcp_server, "_get_cached_metadata", return_value=_METADATA_ROWS):
            result = mcp_server.tool_estate()

    # Not a degradation envelope — a full model.
    assert "available" not in result
    assert result["version"] == "1.0"
    em.validate_estate_model(result, _SCHEMA)

    building_ids = {b["id"] for b in result["buildings"]}
    assert building_ids == {"nook", "workshop"}

    nook = next(b for b in result["buildings"] if b["id"] == "nook")
    wing_types = {w["id"]: w["type"] for w in nook["wings"]}
    assert wing_types.get("wing:dev") == "dev"
    assert wing_types.get("wing:xyzzy-unknown-wing") == "unknown"
    assert "wing:?" in wing_types  # the ? bucket is carried

    # KG counts flowed through from tool_kg_stats.
    assert nook["kg"] == {"entities": 7, "relations": 12}

    # Exactly the one well-formed tunnel survived; malformed entries were skipped.
    assert len(nook["tunnels"]) == 1
    assert nook["tunnels"][0]["endpoints"] == ["wing:dev", "wing:project"]


def test_estate_success_path_no_body_fields(monkeypatch, tmp_path):
    """The model emitted via the tool carries no drawer-body fields."""
    _patch_external_reads(monkeypatch, tmp_path)
    fake_col = object()
    with patch.object(mcp_server, "_get_collection", return_value=fake_col):
        with patch.object(mcp_server, "_get_cached_metadata", return_value=_METADATA_ROWS):
            result = mcp_server.tool_estate()

    forbidden = {"body", "content", "document", "text"}

    def _keys(obj):
        out = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                out.append(k)
                out.extend(_keys(v))
        elif isinstance(obj, list):
            for item in obj:
                out.extend(_keys(item))
        return out

    assert not (set(_keys(result)) & forbidden)


def test_estate_metadata_failure_reason_is_redacted(monkeypatch, tmp_path):
    """The metadata-fetch degradation reason is redacted — an exception carrying
    a home path never leaks into the exported envelope (Codex fold)."""
    _init_nook(monkeypatch, tmp_path)
    fake_col = object()
    with patch.object(mcp_server, "_get_collection", return_value=fake_col):
        with patch.object(
            mcp_server,
            "_get_cached_metadata",
            side_effect=RuntimeError("cannot read /home/secretuser/.sage/nook/chroma.sqlite3"),
        ):
            result = mcp_server.tool_estate()
    assert result["available"] is False
    assert "/home/" not in result["reason"], f"home path leaked in reason: {result['reason']!r}"


def test_estate_build_failure_returns_unavailable(monkeypatch, tmp_path):
    """A build_estate_model exception degrades to ``available: False`` (ADR-0003)."""

    _init_nook(monkeypatch, tmp_path)

    def _boom(*a, **kw):
        raise RuntimeError("schema drift")

    monkeypatch.setattr(em, "build_estate_model", _boom)
    monkeypatch.setattr(mcp_server, "tool_kg_stats", lambda: {"error": "no kg"})
    monkeypatch.setattr(mcp_server, "tool_list_tunnels", lambda *a, **kw: [])
    fake_col = object()
    with patch.object(mcp_server, "_get_collection", return_value=fake_col):
        with patch.object(mcp_server, "_get_cached_metadata", return_value=_METADATA_ROWS):
            result = mcp_server.tool_estate()
    assert result["available"] is False
    assert "schema drift" in result["reason"]


def test_estate_tunnel_strings_redacted_and_sorted(monkeypatch, tmp_path):
    """Tunnel name + endpoints are redacted and endpoints canonical-sorted
    (sec F3 / code F1 fold). A home path in a wing name never leaks, and the
    endpoint pair is sorted so create_tunnel(A,B)==(B,A)."""
    _init_nook(monkeypatch, tmp_path)
    ledger_path = tmp_path / "ledger.json"
    monkeypatch.setattr(em, "_REVISION_PATH", tmp_path / "rev.json")
    real_build = em.build_estate_model

    def _build_isolated(metadata_rows, wing_config, **kwargs):
        kwargs.setdefault("ledger_path", ledger_path)
        kwargs.setdefault("agents_dir", _AGENTS_FIXTURES)
        return real_build(metadata_rows, wing_config, **kwargs)

    monkeypatch.setattr(em, "build_estate_model", _build_isolated)
    monkeypatch.setattr(mcp_server, "tool_kg_stats", lambda: {"error": "no kg"})
    # Real nook_graph shape: a LIST with NESTED source/target.
    monkeypatch.setattr(
        mcp_server,
        "tool_list_tunnels",
        lambda *a, **kw: [
            {
                "label": "/home/secretuser/shared-note",
                "source": {"wing": "zzz-wing", "room": "r1"},
                "target": {"wing": "/home/secretuser/aaa-wing", "room": "r2"},
            }
        ],
    )
    import sage_mcp.telemetry as _tel

    monkeypatch.setattr(_tel, "read_recent", lambda limit=50: [])

    fake_col = object()
    with patch.object(mcp_server, "_get_collection", return_value=fake_col):
        with patch.object(mcp_server, "_get_cached_metadata", return_value=_METADATA_ROWS):
            result = mcp_server.tool_estate()

    nook = next(b for b in result["buildings"] if b["id"] == "nook")
    assert len(nook["tunnels"]) == 1
    tun = nook["tunnels"][0]
    # No home path in name or endpoints.
    assert "/home/" not in tun["name"]
    for ep in tun["endpoints"]:
        assert "/home/" not in ep, f"home path leaked in tunnel endpoint: {ep!r}"
    # Endpoints canonical-sorted.
    assert tun["endpoints"] == sorted(tun["endpoints"])


# ── 4. level clamping ────────────────────────────────────────────────────────


def test_estate_level_clamped_no_crash(monkeypatch, tmp_path):
    """Out-of-range ``level`` is clamped, not rejected — degradation still works."""
    with patch.object(mcp_server, "_get_collection", return_value=None):
        assert mcp_server.tool_estate(level=99)["available"] is False
        assert mcp_server.tool_estate(level=-5)["available"] is False
