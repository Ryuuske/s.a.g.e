"""Tests for sage.layers — Layer0, Layer1, Layer2, Layer3, MemoryStack."""

import os
from unittest.mock import MagicMock, patch

from sage_mcp.layers import Layer0, Layer1, Layer2, Layer3, MemoryStack


# ── Layer0 — with identity file ─────────────────────────────────────────


def test_layer0_reads_identity_file(tmp_path):
    identity_file = tmp_path / "identity.txt"
    identity_file.write_text("I am Atlas, a personal AI assistant for Alice.")
    layer = Layer0(identity_path=str(identity_file))
    text = layer.render()
    assert "Atlas" in text
    assert "Alice" in text


def test_layer0_caches_text(tmp_path):
    identity_file = tmp_path / "identity.txt"
    identity_file.write_text("Hello world")
    layer = Layer0(identity_path=str(identity_file))
    first = layer.render()
    identity_file.write_text("Changed content")
    second = layer.render()
    assert first == second
    assert second == "Hello world"


def test_layer0_missing_file_returns_default(tmp_path):
    missing = str(tmp_path / "nonexistent.txt")
    layer = Layer0(identity_path=missing)
    text = layer.render()
    assert "No identity configured" in text
    assert "identity.txt" in text


def test_layer0_token_estimate(tmp_path):
    identity_file = tmp_path / "identity.txt"
    content = "A" * 400
    identity_file.write_text(content)
    layer = Layer0(identity_path=str(identity_file))
    estimate = layer.token_estimate()
    assert estimate == 100


def test_layer0_token_estimate_empty(tmp_path):
    identity_file = tmp_path / "identity.txt"
    identity_file.write_text("")
    layer = Layer0(identity_path=str(identity_file))
    assert layer.token_estimate() == 0


def test_layer0_strips_whitespace(tmp_path):
    identity_file = tmp_path / "identity.txt"
    identity_file.write_text("  Hello world  \n\n")
    layer = Layer0(identity_path=str(identity_file))
    text = layer.render()
    assert text == "Hello world"


def test_layer0_default_path():
    layer = Layer0()
    expected = os.path.expanduser("~/.sage/identity.txt")
    assert layer.path == expected


# ── Layer1 — mocked chromadb ────────────────────────────────────────────


def _mock_chromadb_for_layer(docs, metas, monkeypatch=None):
    """Return a mock collection whose get() returns docs/metas."""
    mock_col = MagicMock()
    # First batch returns data, second batch returns empty (end of pagination)
    mock_col.get.side_effect = [
        {"documents": docs, "metadatas": metas},
        {"documents": [], "metadatas": []},
    ]
    return mock_col


def test_layer1_no_nook():
    """Layer1 returns helpful message when no nook exists."""
    with patch("sage_mcp.layers.SageConfig") as mock_cfg:
        mock_cfg.return_value.nook_path = "/nonexistent/nook"
        layer = Layer1(nook_path="/nonexistent/nook")
    result = layer.generate()
    assert "No nook found" in result or "No memories" in result


def test_layer1_generates_essential_story():
    docs = [
        "Important memory about project decisions",
        "Key architectural choice for the backend",
    ]
    metas = [
        {"room": "decisions", "source_file": "meeting.txt", "importance": 5},
        {"room": "architecture", "source_file": "design.txt", "importance": 4},
    ]
    mock_col = _mock_chromadb_for_layer(docs, metas)

    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer1(nook_path="/fake")
        result = layer.generate()

    assert "ESSENTIAL STORY" in result
    assert "project decisions" in result


def test_layer1_empty_nook():
    mock_col = MagicMock()
    mock_col.get.return_value = {"documents": [], "metadatas": []}
    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer1(nook_path="/fake")
        result = layer.generate()

    assert "No memories" in result


def test_layer1_with_wing_filter():
    docs = ["Memory about project X"]
    metas = [{"room": "general", "source_file": "x.txt", "importance": 3}]
    mock_col = _mock_chromadb_for_layer(docs, metas)

    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer1(nook_path="/fake", wing="project_x")
        result = layer.generate()

    assert "ESSENTIAL STORY" in result
    # Verify wing filter was passed
    call_kwargs = mock_col.get.call_args_list[0][1]
    assert call_kwargs.get("where") == {"wing": "project_x"}


def test_layer1_truncates_long_snippets():
    docs = ["A" * 300]
    metas = [{"room": "general", "source_file": "long.txt"}]
    mock_col = _mock_chromadb_for_layer(docs, metas)

    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer1(nook_path="/fake")
        result = layer.generate()

    assert "..." in result


def test_layer1_respects_max_chars():
    """L1 stops adding entries once MAX_CHARS is reached."""
    docs = [f"Memory number {i} with substantial content padding here" for i in range(30)]
    metas = [{"room": "general", "source_file": f"f{i}.txt", "importance": 5} for i in range(30)]
    mock_col = _mock_chromadb_for_layer(docs, metas)

    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer1(nook_path="/fake")
        layer.MAX_CHARS = 200  # Very low cap to trigger truncation
        result = layer.generate()

    assert "more in L3 search" in result


def test_layer1_importance_from_various_keys():
    """Layer1 tries importance, emotional_weight, weight keys."""
    docs = ["mem1", "mem2", "mem3"]
    metas = [
        {"room": "r", "emotional_weight": 5},
        {"room": "r", "weight": 1},
        {"room": "r"},  # no weight key, defaults to 3
    ]
    mock_col = _mock_chromadb_for_layer(docs, metas)

    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer1(nook_path="/fake")
        result = layer.generate()

    assert "ESSENTIAL STORY" in result


def test_layer1_batch_exception_breaks():
    """If col.get raises on a batch, loop breaks gracefully."""
    mock_col = MagicMock()
    mock_col.get.side_effect = [
        {"documents": ["doc1"], "metadatas": [{"room": "r"}]},
        RuntimeError("batch error"),
    ]
    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer1(nook_path="/fake")
        result = layer.generate()

    assert "ESSENTIAL STORY" in result


# ── Layer2 — mocked chromadb ────────────────────────────────────────────


def test_layer2_no_nook():
    with patch("sage_mcp.layers.SageConfig") as mock_cfg:
        mock_cfg.return_value.nook_path = "/nonexistent/nook"
        layer = Layer2(nook_path="/nonexistent/nook")
    result = layer.retrieve(wing="test")
    assert "No nook found" in result


def test_layer2_retrieve_with_wing():
    mock_col = MagicMock()
    mock_col.get.return_value = {
        "documents": ["Some memory about the project"],
        "metadatas": [{"room": "backend", "source_file": "notes.txt"}],
    }
    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer2(nook_path="/fake")
        result = layer.retrieve(wing="project")

    assert "ON-DEMAND" in result
    assert "memory about the project" in result


def test_layer2_retrieve_with_room():
    mock_col = MagicMock()
    mock_col.get.return_value = {
        "documents": ["Backend architecture notes"],
        "metadatas": [{"room": "architecture", "source_file": "arch.txt"}],
    }
    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer2(nook_path="/fake")
        result = layer.retrieve(room="architecture")

    assert "ON-DEMAND" in result


def test_layer2_retrieve_wing_and_room():
    mock_col = MagicMock()
    mock_col.get.return_value = {
        "documents": ["Filtered result"],
        "metadatas": [{"room": "backend", "source_file": "x.txt"}],
    }
    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer2(nook_path="/fake")
        result = layer.retrieve(wing="proj", room="backend")

    assert "ON-DEMAND" in result
    call_kwargs = mock_col.get.call_args[1]
    assert "$and" in call_kwargs.get("where", {})


def test_layer2_retrieve_empty():
    mock_col = MagicMock()
    mock_col.get.return_value = {"documents": [], "metadatas": []}
    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer2(nook_path="/fake")
        result = layer.retrieve(wing="missing")

    assert "No drawers found" in result


def test_layer2_retrieve_no_filter():
    mock_col = MagicMock()
    mock_col.get.return_value = {"documents": [], "metadatas": []}
    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer2(nook_path="/fake")
        layer.retrieve()

    # No where filter should be passed
    call_kwargs = mock_col.get.call_args[1]
    assert "where" not in call_kwargs


def test_layer2_retrieve_error():
    mock_col = MagicMock()
    mock_col.get.side_effect = RuntimeError("db error")
    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer2(nook_path="/fake")
        result = layer.retrieve(wing="test")

    assert "Retrieval error" in result


def test_layer2_truncates_long_snippets():
    mock_col = MagicMock()
    mock_col.get.return_value = {
        "documents": ["B" * 400],
        "metadatas": [{"room": "r", "source_file": "s.txt"}],
    }
    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer2(nook_path="/fake")
        result = layer.retrieve(wing="test")

    assert "..." in result


# ── Layer3 — mocked chromadb ────────────────────────────────────────────


def _mock_query_results(docs, metas, dists):
    return {
        "documents": [docs],
        "metadatas": [metas],
        "distances": [dists],
    }


def test_layer3_no_nook():
    with patch("sage_mcp.layers.SageConfig") as mock_cfg:
        mock_cfg.return_value.nook_path = "/nonexistent/nook"
        layer = Layer3(nook_path="/nonexistent/nook")
    result = layer.search("test query")
    assert "No nook found" in result


def test_layer3_search_raw_no_nook():
    with patch("sage_mcp.layers.SageConfig") as mock_cfg:
        mock_cfg.return_value.nook_path = "/nonexistent/nook"
        layer = Layer3(nook_path="/nonexistent/nook")
    result = layer.search_raw("test query")
    assert result == []


def test_layer3_search_with_results():
    mock_col = MagicMock()
    mock_col.query.return_value = _mock_query_results(
        ["Found this important memory"],
        [{"wing": "project", "room": "backend", "source_file": "notes.txt"}],
        [0.2],
    )
    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer3(nook_path="/fake")
        result = layer.search("important")

    assert "SEARCH RESULTS" in result
    assert "important memory" in result
    assert "sim=0.8" in result


def test_layer3_search_no_results():
    mock_col = MagicMock()
    mock_col.query.return_value = _mock_query_results([], [], [])
    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer3(nook_path="/fake")
        result = layer.search("nothing")

    assert "No results found" in result


def test_layer3_search_with_wing_filter():
    mock_col = MagicMock()
    mock_col.query.return_value = _mock_query_results(
        ["result"],
        [{"wing": "proj", "room": "r"}],
        [0.1],
    )
    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer3(nook_path="/fake")
        layer.search("q", wing="proj")

    call_kwargs = mock_col.query.call_args[1]
    assert call_kwargs["where"] == {"wing": "proj"}


def test_layer3_search_with_room_filter():
    mock_col = MagicMock()
    mock_col.query.return_value = _mock_query_results(
        ["result"],
        [{"wing": "w", "room": "backend"}],
        [0.1],
    )
    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer3(nook_path="/fake")
        layer.search("q", room="backend")

    call_kwargs = mock_col.query.call_args[1]
    assert call_kwargs["where"] == {"room": "backend"}


def test_layer3_search_with_wing_and_room():
    mock_col = MagicMock()
    mock_col.query.return_value = _mock_query_results(
        ["result"],
        [{"wing": "proj", "room": "backend"}],
        [0.1],
    )
    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer3(nook_path="/fake")
        layer.search("q", wing="proj", room="backend")

    call_kwargs = mock_col.query.call_args[1]
    assert "$and" in call_kwargs["where"]


def test_layer3_search_error():
    mock_col = MagicMock()
    mock_col.query.side_effect = RuntimeError("search failed")
    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer3(nook_path="/fake")
        result = layer.search("q")

    assert "Search error" in result


def test_layer3_search_truncates_long_docs():
    mock_col = MagicMock()
    mock_col.query.return_value = _mock_query_results(
        ["C" * 400],
        [{"wing": "w", "room": "r", "source_file": "s.txt"}],
        [0.1],
    )
    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer3(nook_path="/fake")
        result = layer.search("q")

    assert "..." in result


def test_layer3_search_raw_returns_dicts():
    mock_col = MagicMock()
    mock_col.query.return_value = _mock_query_results(
        ["doc text"],
        [{"wing": "proj", "room": "backend", "source_file": "f.txt"}],
        [0.3],
    )
    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer3(nook_path="/fake")
        hits = layer.search_raw("q")

    assert len(hits) == 1
    assert hits[0]["text"] == "doc text"
    assert hits[0]["wing"] == "proj"
    assert hits[0]["similarity"] == 0.7
    assert "metadata" in hits[0]


def test_layer3_search_raw_with_filters():
    mock_col = MagicMock()
    mock_col.query.return_value = _mock_query_results(
        ["doc"],
        [{"wing": "w", "room": "r"}],
        [0.1],
    )
    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer3(nook_path="/fake")
        layer.search_raw("q", wing="w", room="r")

    call_kwargs = mock_col.query.call_args[1]
    assert "$and" in call_kwargs["where"]


def test_layer3_search_raw_error():
    mock_col = MagicMock()
    mock_col.query.side_effect = RuntimeError("fail")
    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer3(nook_path="/fake")
        result = layer.search_raw("q")

    assert result == []


# ── MemoryStack ─────────────────────────────────────────────────────────


def test_memory_stack_wake_up(tmp_path):
    identity_file = tmp_path / "identity.txt"
    identity_file.write_text("I am Atlas.")

    with patch("sage_mcp.layers.SageConfig") as mock_cfg:
        mock_cfg.return_value.nook_path = "/nonexistent"
        stack = MemoryStack(
            nook_path="/nonexistent",
            identity_path=str(identity_file),
        )
        result = stack.wake_up()

    assert "Atlas" in result
    # L1 will say no nook found
    assert "No nook" in result or "No memories" in result


def test_memory_stack_wake_up_with_wing(tmp_path):
    identity_file = tmp_path / "identity.txt"
    identity_file.write_text("I am Atlas.")

    with patch("sage_mcp.layers.SageConfig") as mock_cfg:
        mock_cfg.return_value.nook_path = "/nonexistent"
        stack = MemoryStack(
            nook_path="/nonexistent",
            identity_path=str(identity_file),
        )
        result = stack.wake_up(wing="my_project")

    assert stack.l1.wing == "my_project"
    assert "Atlas" in result


def test_memory_stack_recall(tmp_path):
    identity_file = tmp_path / "identity.txt"
    identity_file.write_text("I am Atlas.")

    with patch("sage_mcp.layers.SageConfig") as mock_cfg:
        mock_cfg.return_value.nook_path = "/nonexistent"
        stack = MemoryStack(
            nook_path="/nonexistent",
            identity_path=str(identity_file),
        )
        result = stack.recall(wing="test")

    assert "No nook found" in result


def test_memory_stack_search(tmp_path):
    identity_file = tmp_path / "identity.txt"
    identity_file.write_text("I am Atlas.")

    with patch("sage_mcp.layers.SageConfig") as mock_cfg:
        mock_cfg.return_value.nook_path = "/nonexistent"
        stack = MemoryStack(
            nook_path="/nonexistent",
            identity_path=str(identity_file),
        )
        result = stack.search("test query")

    assert "No nook found" in result


def test_memory_stack_status(tmp_path):
    identity_file = tmp_path / "identity.txt"
    identity_file.write_text("I am Atlas.")

    with patch("sage_mcp.layers.SageConfig") as mock_cfg:
        mock_cfg.return_value.nook_path = "/nonexistent"
        stack = MemoryStack(
            nook_path="/nonexistent",
            identity_path=str(identity_file),
        )
        result = stack.status()

    assert result["nook_path"] == "/nonexistent"
    assert result["total_drawers"] == 0
    assert "L0_identity" in result
    assert "L1_essential" in result
    assert "L2_on_demand" in result
    assert "L3_deep_search" in result


def test_memory_stack_status_with_nook(tmp_path):
    identity_file = tmp_path / "identity.txt"
    identity_file.write_text("I am Atlas.")

    mock_col = MagicMock()
    mock_col.count.return_value = 42
    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        stack = MemoryStack(
            nook_path="/fake",
            identity_path=str(identity_file),
        )
        result = stack.status()

    assert result["total_drawers"] == 42
    assert result["L0_identity"]["exists"] is True


# ── Layer1 / Layer2 None-metadata guards ───────────────────────────────
#
# Chroma 1.5.x can return ``None`` inside the ``metadatas`` / ``documents``
# lists for partially-flushed rows. The Layer1.generate() and
# Layer2.retrieve() loops previously called ``meta.get(...)`` without
# coercing, raising ``AttributeError: 'NoneType' object has no attribute
# 'get'`` and blowing up the whole wake-up render. These tests guard that
# the loops tolerate the None entries and render the rest of the result.


def test_layer1_handles_none_metadata():
    """Layer1.generate tolerates None entries in the metadatas list."""
    docs = ["important memory", "another memory"]
    metas = [{"room": "decisions", "source_file": "a.txt"}, None]
    mock_col = _mock_chromadb_for_layer(docs, metas)

    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer1(nook_path="/fake")
        # Should not raise AttributeError on the None entry.
        result = layer.generate()

    assert "ESSENTIAL STORY" in result
    assert "important memory" in result


def test_layer1_handles_none_document():
    """Layer1.generate tolerates None entries in the documents list."""
    docs = ["first doc", None]
    metas = [
        {"room": "r", "source_file": "a.txt"},
        {"room": "r", "source_file": "b.txt"},
    ]
    mock_col = _mock_chromadb_for_layer(docs, metas)

    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer1(nook_path="/fake")
        result = layer.generate()

    assert result  # Render succeeded despite the None document.


def test_layer2_handles_none_metadata():
    """Layer2.retrieve tolerates None entries in the metadatas list."""
    mock_col = MagicMock()
    mock_col.get.return_value = {
        "documents": ["first doc", "second doc"],
        "metadatas": [{"room": "r", "source_file": "a.txt"}, None],
    }

    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = "/fake"
        layer = Layer2(nook_path="/fake")
        # Should not raise AttributeError on the None entry.
        result = layer.retrieve()

    assert "L2 — ON-DEMAND" in result


# ── FIX 1c: secret scrub applied to full Tier-0 block ───────────────────


def _make_mock_l1_with_doc(doc: str) -> MagicMock:
    """Return a mock ChromaDB collection that yields one drawer with ``doc``."""
    mock_col = MagicMock()
    mock_col.get.side_effect = [
        {
            "documents": [doc],
            "metadatas": [{"room": "handoff", "source_file": "handoff.md", "importance": 5}],
        },
        {"documents": [], "metadatas": []},
    ]
    return mock_col


def test_tier0_scrubs_token_in_l1_handoff_drawer(tmp_path):
    """An L1 handoff drawer containing a fake ghp_ token yields [REDACTED] in Tier-0 block.

    FIX 1c: scrub_secrets is applied to the full assembled block post-assembly,
    covering L0 identity + L1 drawer content, not just the registry section.
    """
    identity_file = tmp_path / "identity.txt"
    identity_file.write_text("I am Atlas, the test orchestrator.", encoding="utf-8")

    # A handoff drawer that quotes a fake GitHub token — the exact shape the
    # WI-3 brief names as the motivating example.
    fake_token_doc = "Handoff: token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcd01 stored for auth"

    mock_col = _make_mock_l1_with_doc(fake_token_doc)
    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = str(tmp_path / "nook")
        stack = MemoryStack(
            nook_path=str(tmp_path / "nook"),
            identity_path=str(identity_file),
        )
        block = stack.assemble_tier0(wing=None, repo_root=None)

    assert "ghp_" not in block.text, (
        "GitHub token prefix leaked into Tier-0 block — scrub_secrets not applied to L1 content."
    )
    assert "[REDACTED]" in block.text, "Expected [REDACTED] marker in Tier-0 block."


# ── FIX 2: budget enforcement ────────────────────────────────────────────


def _make_mock_l1_many(n: int) -> MagicMock:
    """Return a mock collection with ``n`` long drawers to force oversizing."""
    docs = [f"Very important memory number {i}: " + "X" * 300 for i in range(n)]
    metas = [{"room": "general", "source_file": f"file{i}.md", "importance": 5} for i in range(n)]
    mock_col = MagicMock()
    mock_col.get.side_effect = [
        {"documents": docs, "metadatas": metas},
        {"documents": [], "metadatas": []},
    ]
    return mock_col


def test_tier0_budget_enforced_oversized_nook(tmp_path):
    """An oversized nook (many long L1 drawers) produces a block ≤ budget.

    FIX 2: TIER0_TOKEN_BUDGET is enforced; the block is trimmed and a trim
    marker is emitted when the assembled content exceeds the ceiling.

    Uses a tiny custom budget so the test does not depend on the absolute
    size of L1 output (which is capped internally by Layer1.MAX_CHARS).
    The identity text alone exceeds the micro-budget, proving the enforcement
    path runs and trims correctly.
    """
    identity_file = tmp_path / "identity.txt"
    # Identity text ~200 chars → ~50 tokens → exceeds micro_budget=10.
    identity_file.write_text("I am Atlas, the test orchestrator. " * 6, encoding="utf-8")

    mock_col = _make_mock_l1_many(15)
    # Use a very small budget to force the trim path reliably.
    micro_budget = 10  # tokens (40 chars)
    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = str(tmp_path / "nook")
        stack = MemoryStack(
            nook_path=str(tmp_path / "nook"),
            identity_path=str(identity_file),
        )
        block = stack.assemble_tier0(wing=None, repo_root=None, budget=micro_budget)

    # The trim marker itself adds a small overhead beyond the budget.
    # Check the content portion (strip the trailing marker) is within budget.
    content_text = block.text
    marker = "[Tier-0 trimmed to budget]"
    assert marker in content_text, (
        "Expected trim marker in oversized block — budget trim path not taken."
    )
    # Content before the marker must be ≤ char_limit.
    content_before_marker = content_text[: content_text.rfind(marker)]
    content_tokens = len(content_before_marker) // 4
    assert content_tokens <= micro_budget, (
        f"Trimmed content ({content_tokens} tokens) exceeds budget ({micro_budget}). "
        "FIX 2 budget enforcement is not working."
    )


# ── FIX 4: deterministic tiebreaker for L1 sort ─────────────────────────


def test_layer1_sort_is_deterministic_with_equal_importance(tmp_path):
    """Layer1 sort is byte-stable when drawers have identical importance scores.

    FIX 4: explicit secondary tiebreaker (source_file) prevents backend-order
    dependence and keeps the tier0_block_stable proxy accurate.
    """
    docs = [f"Memory {i} content" for i in range(5)]
    # All same importance — tiebreaker must produce stable order.
    metas = [{"room": "general", "source_file": f"zz{i}.md", "importance": 3} for i in range(5)]

    def _fresh_col():
        mock_col = MagicMock()
        mock_col.get.side_effect = [
            {"documents": docs, "metadatas": metas},
            {"documents": [], "metadatas": []},
        ]
        return mock_col

    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection") as mock_get_col,
    ):
        mock_cfg.return_value.nook_path = str(tmp_path / "nook")
        mock_get_col.side_effect = lambda *a, **kw: _fresh_col()
        layer = Layer1(nook_path=str(tmp_path / "nook"))
        result1 = layer.generate()
        result2 = layer.generate()

    assert result1 == result2, "Layer1 output is not deterministic for equal-importance drawers."


def test_layer1_sort_is_deterministic_with_same_source_file(tmp_path):
    """Layer1 sort is byte-stable when multiple drawers share the same source_file.

    F#1: the tertiary tiebreaker (hash of drawer text) guarantees full
    determinism even when the secondary key (source_file) is identical for
    multiple drawers — ChromaDB backend iteration order must not matter.
    """
    # Three drawers with same importance AND same source_file — only text differs.
    docs = ["Drawer alpha content", "Drawer beta content", "Drawer gamma content"]
    metas = [
        {"room": "general", "source_file": "shared.md", "importance": 5},
        {"room": "general", "source_file": "shared.md", "importance": 5},
        {"room": "general", "source_file": "shared.md", "importance": 5},
    ]

    def _fresh_col():
        mock_col = MagicMock()
        mock_col.get.side_effect = [
            {"documents": docs, "metadatas": metas},
            {"documents": [], "metadatas": []},
        ]
        return mock_col

    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection") as mock_get_col,
    ):
        mock_cfg.return_value.nook_path = str(tmp_path / "nook")
        mock_get_col.side_effect = lambda *a, **kw: _fresh_col()
        layer = Layer1(nook_path=str(tmp_path / "nook"))
        result1 = layer.generate()
        result2 = layer.generate()

    assert result1 == result2, (
        "Layer1 output is not deterministic when drawers share the same source_file. "
        "The tertiary hash tiebreaker (F#1) must produce stable order."
    )


# ── FIX 6: l1.wing side-effect ───────────────────────────────────────────


def test_assemble_tier0_does_not_mutate_l1_wing(tmp_path):
    """assemble_tier0(wing=X) must not permanently mutate self.l1.wing.

    FIX 6: the wing is save/restored around the l1.generate() call so repeated
    calls with different wings do not leak state.
    """
    identity_file = tmp_path / "identity.txt"
    identity_file.write_text("I am Atlas.", encoding="utf-8")

    mock_col = MagicMock()
    mock_col.get.return_value = {"documents": [], "metadatas": []}

    with (
        patch("sage_mcp.layers.SageConfig") as mock_cfg,
        patch("sage_mcp.layers._get_collection", return_value=mock_col),
    ):
        mock_cfg.return_value.nook_path = str(tmp_path / "nook")
        stack = MemoryStack(
            nook_path=str(tmp_path / "nook"),
            identity_path=str(identity_file),
        )
        original_wing = stack.l1.wing  # None by default
        stack.assemble_tier0(wing="wing-alpha", repo_root=None)

    # After the call, l1.wing must be back to its original value.
    assert stack.l1.wing == original_wing, (
        f"assemble_tier0 mutated l1.wing: expected {original_wing!r}, "
        f"got {stack.l1.wing!r}. FIX 6 side-effect not resolved."
    )


# ── Additional layers unit tests (relocated from test_bootstrap.py) ───────────


def test_layers_build_registry_section_no_root():
    """_build_registry_section returns empty string when repo_root is None/empty."""
    from sage_mcp.layers import _build_registry_section

    text, count = _build_registry_section(None)
    assert text == ""
    assert count == 0

    text2, count2 = _build_registry_section("")
    assert text2 == ""
    assert count2 == 0


def test_layers_build_registry_section_exception(tmp_path):
    """_build_registry_section returns empty on exception from build_registry."""
    from unittest.mock import patch
    from sage_mcp.layers import _build_registry_section

    with patch(
        "sage_mcp.extensions.skill_registry.build_registry",
        side_effect=RuntimeError("registry exploded"),
    ):
        text, count = _build_registry_section(str(tmp_path))

    assert text == ""
    assert count == 0
