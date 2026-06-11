"""
test_searcher.py -- Tests for search_memories() (the programmatic API).

Uses the real ChromaDB fixtures from conftest.py for integration tests,
plus mock-based tests for error paths.
"""

from unittest.mock import MagicMock, patch


from sage_mcp.searcher import search_memories


# ── search_memories (API) ──────────────────────────────────────────────


class TestSearchMemories:
    def test_basic_search(self, nook_path, seeded_collection):
        result = search_memories("JWT authentication", nook_path)
        assert "results" in result
        assert len(result["results"]) > 0
        assert result["query"] == "JWT authentication"

    def test_wing_filter(self, nook_path, seeded_collection):
        result = search_memories("planning", nook_path, wing="notes")
        assert all(r["wing"] == "notes" for r in result["results"])

    def test_room_filter(self, nook_path, seeded_collection):
        result = search_memories("database", nook_path, room="backend")
        assert all(r["room"] == "backend" for r in result["results"])

    def test_wing_and_room_filter(self, nook_path, seeded_collection):
        result = search_memories("code", nook_path, wing="project", room="frontend")
        assert all(r["wing"] == "project" and r["room"] == "frontend" for r in result["results"])

    def test_n_results_limit(self, nook_path, seeded_collection):
        result = search_memories("code", nook_path, n_results=2)
        assert len(result["results"]) <= 2

    def test_no_nook_returns_error(self, tmp_path):
        result = search_memories("anything", str(tmp_path / "missing"))
        assert "error" in result

    def test_result_fields(self, nook_path, seeded_collection):
        result = search_memories("authentication", nook_path)
        hit = result["results"][0]
        assert "text" in hit
        assert "wing" in hit
        assert "room" in hit
        assert "source_file" in hit
        assert "similarity" in hit
        assert isinstance(hit["similarity"], float)
        assert "created_at" in hit

    def test_created_at_contains_filed_at(self, nook_path, seeded_collection):
        """created_at surfaces the filed_at metadata from the drawer."""
        result = search_memories("JWT authentication", nook_path)
        hit = result["results"][0]
        assert hit["created_at"] == "2026-01-01T00:00:00"

    def test_created_at_fallback_when_filed_at_missing(self):
        """created_at defaults to 'unknown' when filed_at is absent."""
        mock_col = MagicMock()
        mock_col.query.return_value = {
            "ids": [["drawer_no_date"]],
            "documents": [["Some text without a date"]],
            "metadatas": [[{"wing": "project", "room": "backend", "source_file": "x.py"}]],
            "distances": [[0.1]],
        }

        with patch("sage_mcp.searcher.get_collection", return_value=mock_col):
            result = search_memories("test", "/fake/path")
        hit = result["results"][0]
        assert hit["created_at"] == "unknown"

    def test_search_memories_query_error(self):
        """search_memories returns error dict when query raises."""
        mock_col = MagicMock()
        mock_col.query.side_effect = RuntimeError("query failed")

        with patch("sage_mcp.searcher.get_collection", return_value=mock_col):
            result = search_memories("test", "/fake/path")
        assert "error" in result
        assert "query failed" in result["error"]

    def test_search_memories_vector_path_uses_explicit_collection_name(self):
        mock_col = MagicMock()
        mock_col.query.return_value = {
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
            "ids": [[]],
        }

        with patch("sage_mcp.searcher.get_collection", return_value=mock_col) as get_collection:
            search_memories("test", "/fake/path", collection_name="custom_drawers")

        get_collection.assert_called_once_with(
            "/fake/path",
            collection_name="custom_drawers",
            create=False,
        )

    def test_search_memories_filters_in_result(self, nook_path, seeded_collection):
        result = search_memories("test", nook_path, wing="project", room="backend")
        assert result["filters"]["wing"] == "project"
        assert result["filters"]["room"] == "backend"

    def test_search_memories_handles_none_metadata(self):
        """API path: `None` entries in the drawer results' metadatas list must
        fall back to the sentinel strings (wing/room 'unknown', source '?')
        rather than raising `AttributeError: 'NoneType' object has no
        attribute 'get'` while the rest of the result set renders."""
        mock_col = MagicMock()
        mock_col.query.return_value = {
            "documents": [["first doc", "second doc"]],
            "metadatas": [[{"source_file": "a.md", "wing": "w", "room": "r"}, None]],
            "distances": [[0.1, 0.2]],
            "ids": [["d1", "d2"]],
        }

        def mock_get_collection(path, collection_name=None, create=False):
            # First call: drawers. Second call: closets — raise so hybrid
            # degrades to pure drawer search (the catch block covers it).
            if not hasattr(mock_get_collection, "_called"):
                mock_get_collection._called = True
                return mock_col
            raise RuntimeError("no closets")

        with patch("sage_mcp.searcher.get_collection", side_effect=mock_get_collection):
            result = search_memories("anything", "/fake/path")
        assert "results" in result
        assert len(result["results"]) == 2
        # The None-metadata hit renders with sentinel values, not a crash.
        none_hit = result["results"][1]
        assert none_hit["text"] == "second doc"
        assert none_hit["wing"] == "unknown"
        assert none_hit["room"] == "unknown"

    def test_strength_field_present_in_search_results(self, nook_path, seeded_collection):
        """search_memories results must carry a 'strength' field in every hit.

        Regression guard for searcher.py line ~1048: the entry dict built in
        search_memories must include ``"strength": float(meta.get("strength", 1.0))``.
        If that line is removed or the key renamed, _hybrid_rank's _get_strength
        falls back to DRAWER_STRENGTH_DEFAULT for every hit, making strength_norm=0
        for all results and neutering WI-6 decay signal at retrieval time.
        """
        result = search_memories("authentication", nook_path)
        assert result.get("results"), "no results returned"
        for hit in result["results"]:
            assert "strength" in hit, (
                f"'strength' missing from search_memories result hit: {list(hit.keys())}. "
                "searcher.py entry dict must include strength=float(meta.get('strength',1.0))."
            )

    def test_non_default_strength_carried_through_search_results(self):
        """A drawer stored with a non-default strength must surface that value in
        the search_memories result dict.

        Regression guard for the full carry-through path: if searcher.py stops
        reading meta.get('strength') and always emits 1.0, this test catches it.
        """
        mock_col = MagicMock()
        mock_col.query.return_value = {
            "ids": [["drawer_with_custom_strength"]],
            "documents": [["Some text about JWT tokens"]],
            "metadatas": [
                [
                    {
                        "wing": "project",
                        "room": "backend",
                        "source_file": "auth.py",
                        "filed_at": "2026-01-01T00:00:00",
                        "strength": 0.42,
                    }
                ]
            ],
            "distances": [[0.15]],
        }

        with patch("sage_mcp.searcher.get_collection", return_value=mock_col):
            result = search_memories("JWT tokens", "/fake/path")

        assert result.get("results"), "no results returned"
        hit = result["results"][0]
        assert "strength" in hit, "'strength' key missing from search_memories result"
        assert abs(hit["strength"] - 0.42) < 1e-6, (
            f"Expected strength=0.42 from stored metadata but got {hit['strength']}. "
            "The searcher.py entry dict must carry meta.get('strength', 1.0) through; "
            "a regression here means _hybrid_rank sees uniform strength and WI-6 "
            "decay has no effect on retrieval ranking."
        )

    def test_effective_distance_clamped_to_valid_cosine_range(self):
        """A strong closet boost (up to 0.40) applied to a low-distance drawer
        can drive ``dist - boost`` negative. That violates the cosine-distance
        invariant ``[0, 2]``: the API returns ``similarity > 1.0`` and the
        internal ``_sort_key`` sinks below ordinary positive distances,
        inverting the ranking so the best hybrid matches sort last.

        With the clamp, ``effective_distance`` stays in ``[0, 2]``,
        ``similarity`` stays in ``[0, 1]``, and the sort order is stable.
        """
        # Drawer a.md gets a tiny base distance (0.08) — nearly exact match.
        # Drawer b.md gets a larger base distance (0.35).
        drawers_col = MagicMock()
        drawers_col.query.return_value = {
            "documents": [["doc-a", "doc-b"]],
            "metadatas": [
                [
                    {"source_file": "a.md", "wing": "w", "room": "r", "chunk_index": 0},
                    {"source_file": "b.md", "wing": "w", "room": "r", "chunk_index": 0},
                ]
            ],
            "distances": [[0.08, 0.35]],
            "ids": [["d-a", "d-b"]],
        }
        # A strong closet at rank 0 points at a.md → boost = 0.40,
        # which exceeds a.md's base distance and would go negative without
        # the clamp. No closet for b.md.
        closets_col = MagicMock()
        closets_col.query.return_value = {
            "documents": [["closet-preview-a"]],
            "metadatas": [[{"source_file": "a.md"}]],
            "distances": [[0.2]],  # within CLOSET_DISTANCE_CAP (1.5)
            "ids": [["c-a"]],
        }

        with (
            patch("sage_mcp.searcher.get_collection", return_value=drawers_col),
            patch("sage_mcp.searcher.get_closets_collection", return_value=closets_col),
        ):
            result = search_memories("query", "/fake/path", n_results=5)

        hits = result["results"]
        assert hits, "should return results"

        # Invariants on every hit.
        for h in hits:
            assert 0.0 <= h["similarity"] <= 1.0, (
                f"similarity out of range: {h['similarity']} for {h['source_file']}"
            )
            assert 0.0 <= h["effective_distance"] <= 2.0, (
                f"effective_distance out of range: {h['effective_distance']} for {h['source_file']}"
            )

        # With the clamp, the closet-boosted a.md still ranks ahead of b.md —
        # the boost still wins, but it no longer flips the ranking.
        assert hits[0]["source_file"] == "a.md"
        assert hits[0]["matched_via"] == "drawer+closet"


# ── BM25 internals: None / empty document safety ─────────────────────


class TestBM25NoneSafety:
    """Regression tests for the AttributeError observed in production when
    Chroma returned ``None`` documents inside a hybrid-rerank pass.

    Trace from the daemon log (2026-04-24 21:07:05):
        File "sage/searcher.py", line 81, in _bm25_scores
            tokenized = [_tokenize(d) for d in documents]
        File "sage/searcher.py", line 52, in _tokenize
            return _TOKEN_RE.findall(text.lower())
        AttributeError: 'NoneType' object has no attribute 'lower'
    """

    def test_tokenize_handles_none(self):
        from sage_mcp.searcher import _tokenize

        assert _tokenize(None) == []

    def test_tokenize_handles_empty_string(self):
        from sage_mcp.searcher import _tokenize

        assert _tokenize("") == []

    def test_bm25_scores_does_not_crash_on_none_documents(self):
        """A ``None`` mixed into the corpus must yield score 0.0 for that doc
        and finite scores for the rest, not raise AttributeError."""
        from sage_mcp.searcher import _bm25_scores

        scores = _bm25_scores(
            "postgres migration", ["postgres migration done", None, "kafka rebalance"]
        )
        assert len(scores) == 3
        assert scores[1] == 0.0
        assert scores[0] > 0.0


# ── nook-state handling + #1498 read-must-not-create ────────────────────


class TestSearchMemoriesNookState:
    def test_state_b_no_db_returns_error_and_does_not_create_file(self, tmp_path):
        """#1498: a read against a dir with no chroma.sqlite3 must NOT lazily
        create the db (the State-B backend guard), and must say 'run sage mine'."""
        nook = tmp_path / "nook"
        nook.mkdir()
        result = search_memories("anything", str(nook))
        assert "error" in result
        assert "mine" in result["hint"].lower()
        assert not (nook / "chroma.sqlite3").exists()

    def test_state_a_no_dir_returns_no_nook_found(self, tmp_path):
        result = search_memories("anything", str(tmp_path / "absent"))
        assert "error" in result
        assert "No nook found" in result["error"]

    def test_state_c_initialized_but_empty_says_mine_not_init(self, monkeypatch, tmp_path):
        from sage_mcp.backends import CollectionNotInitializedError

        nook = tmp_path / "nook"
        nook.mkdir()
        (nook / "chroma.sqlite3").write_text("")

        def raise_cnie(*a, **k):
            raise CollectionNotInitializedError(str(nook))

        monkeypatch.setattr("sage_mcp.searcher.get_collection", raise_cnie)
        result = search_memories("anything", str(nook))
        assert "initialized but empty" in result["error"]
        assert "sage mine" in result["hint"]


class TestUnsupportedMetricWarning:
    def test_cosine_no_warning(self):
        from sage_mcp.searcher import _unsupported_metric_warning

        col = MagicMock()
        col.metadata = {"hnsw:space": "cosine"}
        assert _unsupported_metric_warning(col) is None

    def test_l2_warns_with_repair_pointer(self):
        from sage_mcp.searcher import _unsupported_metric_warning

        col = MagicMock()
        col.metadata = {"hnsw:space": "l2"}
        warning = _unsupported_metric_warning(col)
        assert warning is not None
        assert "sage repair" in warning

    def test_no_metadata_table_is_suspect(self):
        from sage_mcp.searcher import _unsupported_metric_warning

        col = MagicMock()
        col.metadata = None
        assert _unsupported_metric_warning(col) is None
