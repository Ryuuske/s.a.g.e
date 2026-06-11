"""Tests for sage.dedup — near-duplicate drawer detection and removal."""

from unittest.mock import MagicMock, patch

import pytest

from sage_mcp import dedup
from sage_mcp.dedup import (
    EpisodicRecord,
    GateDecision,
    VectorDisabledError,
    WriteBackCategory,
    WriteBackDecision,
    make_query_fn,
    write_back_gate,
)


# ── get_source_groups ─────────────────────────────────────────────────


def test_get_source_groups_basic():
    col = MagicMock()
    col.count.return_value = 5
    col.get.side_effect = [
        {
            "ids": ["d1", "d2", "d3", "d4", "d5"],
            "metadatas": [
                {"source_file": "a.txt"},
                {"source_file": "a.txt"},
                {"source_file": "a.txt"},
                {"source_file": "a.txt"},
                {"source_file": "a.txt"},
            ],
        },
        {"ids": []},
    ]
    groups = dedup.get_source_groups(col, min_count=5)
    assert "a.txt" in groups
    assert len(groups["a.txt"]) == 5


def test_get_source_groups_below_min():
    col = MagicMock()
    col.count.return_value = 2
    col.get.side_effect = [
        {
            "ids": ["d1", "d2"],
            "metadatas": [
                {"source_file": "a.txt"},
                {"source_file": "a.txt"},
            ],
        },
        {"ids": []},
    ]
    groups = dedup.get_source_groups(col, min_count=5)
    assert len(groups) == 0


def test_get_source_groups_source_filter():
    col = MagicMock()
    col.count.return_value = 6
    col.get.side_effect = [
        {
            "ids": ["d1", "d2", "d3", "d4", "d5", "d6"],
            "metadatas": [
                {"source_file": "project_a.txt"},
                {"source_file": "project_a.txt"},
                {"source_file": "project_a.txt"},
                {"source_file": "project_a.txt"},
                {"source_file": "project_a.txt"},
                {"source_file": "other.txt"},
            ],
        },
        {"ids": []},
    ]
    groups = dedup.get_source_groups(col, min_count=5, source_pattern="project_a")
    assert "project_a.txt" in groups
    assert "other.txt" not in groups


def test_get_source_groups_wing_filter():
    col = MagicMock()
    col.count.return_value = 5
    col.get.side_effect = [
        {
            "ids": ["d1", "d2", "d3", "d4", "d5"],
            "metadatas": [
                {"source_file": "a.txt"},
                {"source_file": "a.txt"},
                {"source_file": "a.txt"},
                {"source_file": "a.txt"},
                {"source_file": "a.txt"},
            ],
        },
        {"ids": []},
    ]
    dedup.get_source_groups(col, min_count=5, wing="my_wing")
    # Verify where filter was passed
    first_call = col.get.call_args_list[0]
    assert first_call.kwargs.get("where") == {"wing": "my_wing"}


def test_get_source_groups_missing_source_file():
    col = MagicMock()
    col.count.return_value = 5
    col.get.side_effect = [
        {
            "ids": ["d1", "d2", "d3", "d4", "d5"],
            "metadatas": [{}, {}, {}, {}, {}],
        },
        {"ids": []},
    ]
    groups = dedup.get_source_groups(col, min_count=5)
    assert "unknown" in groups


# ── dedup_source_group ────────────────────────────────────────────────


def test_dedup_source_group_all_unique():
    col = MagicMock()
    col.get.return_value = {
        "ids": ["d1", "d2"],
        "documents": ["long document one content here", "different document two here"],
        "metadatas": [{"wing": "a"}, {"wing": "a"}],
    }
    col.query.return_value = {
        "ids": [["d1"]],
        "distances": [[0.8]],  # far apart = unique
    }
    kept, deleted = dedup.dedup_source_group(col, ["d1", "d2"], threshold=0.15, dry_run=True)
    assert len(kept) == 2
    assert len(deleted) == 0


def test_dedup_source_group_with_duplicate():
    col = MagicMock()
    col.get.return_value = {
        "ids": ["d1", "d2"],
        "documents": [
            "long document content that is fairly long",
            "long document content that is fairly long",
        ],
        "metadatas": [{"wing": "a"}, {"wing": "a"}],
    }
    col.query.return_value = {
        "ids": [["d1"]],
        "distances": [[0.05]],  # very close = duplicate
    }
    kept, deleted = dedup.dedup_source_group(col, ["d1", "d2"], threshold=0.15, dry_run=True)
    assert len(kept) == 1
    assert len(deleted) == 1


def test_dedup_source_group_short_docs_deleted():
    col = MagicMock()
    col.get.return_value = {
        "ids": ["d1", "d2"],
        "documents": ["long enough document to keep in the nook", "tiny"],
        "metadatas": [{"wing": "a"}, {"wing": "a"}],
    }
    kept, deleted = dedup.dedup_source_group(col, ["d1", "d2"], threshold=0.15, dry_run=True)
    assert "d2" in deleted  # too short


def test_dedup_source_group_empty_doc_deleted():
    col = MagicMock()
    col.get.return_value = {
        "ids": ["d1", "d2"],
        "documents": ["real document content here that is long enough", None],
        "metadatas": [{"wing": "a"}, {"wing": "a"}],
    }
    kept, deleted = dedup.dedup_source_group(col, ["d1", "d2"], threshold=0.15, dry_run=True)
    assert "d2" in deleted


def test_dedup_source_group_live_deletes():
    col = MagicMock()
    col.get.return_value = {
        "ids": ["d1", "d2"],
        "documents": ["long document content here enough", "long document content here enough"],
        "metadatas": [{"wing": "a"}, {"wing": "a"}],
    }
    col.query.return_value = {
        "ids": [["d1"]],
        "distances": [[0.05]],
    }
    kept, deleted = dedup.dedup_source_group(col, ["d1", "d2"], threshold=0.15, dry_run=False)
    col.delete.assert_called_once()


def test_dedup_source_group_query_failure_keeps():
    col = MagicMock()
    col.get.return_value = {
        "ids": ["d1", "d2"],
        "documents": [
            "long document one content here enough",
            "long document two content here enough",
        ],
        "metadatas": [{"wing": "a"}, {"wing": "a"}],
    }
    col.query.side_effect = Exception("query failed")
    kept, deleted = dedup.dedup_source_group(col, ["d1", "d2"], threshold=0.15, dry_run=True)
    assert len(kept) == 2  # both kept on error


# ── show_stats ────────────────────────────────────────────────────────


def _install_mock_backend(mock_backend_cls, collection):
    mock_backend = MagicMock()
    mock_backend.get_collection.return_value = collection
    mock_backend_cls.return_value = mock_backend
    return mock_backend


@patch("sage_mcp.dedup.ChromaBackend")
def test_show_stats(mock_backend_cls, tmp_path):
    mock_col = MagicMock()
    mock_col.count.return_value = 5
    mock_col.get.side_effect = [
        {
            "ids": ["d1", "d2", "d3", "d4", "d5"],
            "metadatas": [
                {"source_file": "a.txt"},
                {"source_file": "a.txt"},
                {"source_file": "a.txt"},
                {"source_file": "a.txt"},
                {"source_file": "a.txt"},
            ],
        },
        {"ids": []},
    ]
    _install_mock_backend(mock_backend_cls, mock_col)

    dedup.show_stats(nook_path=str(tmp_path))  # should not raise


# ── dedup_nook ──────────────────────────────────────────────────────


@patch("sage_mcp.dedup.dedup_source_group")
@patch("sage_mcp.dedup.get_source_groups")
@patch("sage_mcp.dedup.ChromaBackend")
def test_dedup_nook_dry_run(mock_backend_cls, mock_groups, mock_dedup_group, tmp_path):
    mock_col = MagicMock()
    mock_col.count.return_value = 10
    _install_mock_backend(mock_backend_cls, mock_col)

    mock_groups.return_value = {"a.txt": ["d1", "d2", "d3", "d4", "d5"]}
    mock_dedup_group.return_value = (["d1", "d2", "d3"], ["d4", "d5"])

    dedup.dedup_nook(nook_path=str(tmp_path), dry_run=True)
    mock_dedup_group.assert_called_once()


@patch("sage_mcp.dedup.dedup_source_group")
@patch("sage_mcp.dedup.get_source_groups")
@patch("sage_mcp.dedup.ChromaBackend")
def test_dedup_nook_with_wing(mock_backend_cls, mock_groups, mock_dedup_group, tmp_path):
    mock_col = MagicMock()
    mock_col.count.return_value = 10
    _install_mock_backend(mock_backend_cls, mock_col)

    mock_groups.return_value = {}
    dedup.dedup_nook(nook_path=str(tmp_path), wing="test_wing", dry_run=True)
    mock_groups.assert_called_once_with(mock_col, 5, None, wing="test_wing")


@patch("sage_mcp.dedup.dedup_source_group")
@patch("sage_mcp.dedup.get_source_groups")
@patch("sage_mcp.dedup.ChromaBackend")
def test_dedup_nook_no_groups(mock_backend_cls, mock_groups, mock_dedup_group, tmp_path):
    mock_col = MagicMock()
    mock_col.count.return_value = 3
    _install_mock_backend(mock_backend_cls, mock_col)

    mock_groups.return_value = {}
    dedup.dedup_nook(nook_path=str(tmp_path), dry_run=True)
    mock_dedup_group.assert_not_called()


# ── write_back_gate ───────────────────────────────────────────────────────────


def _make_query_fn(*matches):
    """Return a query_fn that always returns the given match list."""

    def query_fn(text, n_results):
        return list(matches)

    return query_fn


def test_write_back_gate_store_novel():
    """Novel content (no near-matches) → STORE."""
    result = write_back_gate(
        "Decided to use ChromaDB as the primary vector store for all wings.",
        _make_query_fn(),  # empty — no existing drawers
    )
    assert result.decision == GateDecision.STORE
    assert result.decision == "STORE"  # str equality via Enum(str)
    assert result.top_match_id is None
    assert result.top_similarity == 0.0


def test_write_back_gate_skip_near_duplicate():
    """Similarity ≥ 0.90 → SKIP (near-exact duplicate)."""
    result = write_back_gate(
        "Decided to use ChromaDB as the primary vector store for all wings.",
        _make_query_fn(
            {"id": "drawer-abc", "similarity": 0.95, "wing": "dev", "room": "decisions"}
        ),
    )
    assert result.decision == GateDecision.SKIP
    assert result.decision == "SKIP"
    assert result.top_match_id == "drawer-abc"
    assert result.top_similarity == 0.95


def test_write_back_gate_merge_candidate():
    """Similarity in [0.85, 0.90) → MERGE-CANDIDATE."""
    result = write_back_gate(
        "Decided to use ChromaDB as the primary vector store.",
        _make_query_fn(
            {"id": "drawer-xyz", "similarity": 0.87, "wing": "dev", "room": "decisions"}
        ),
    )
    assert result.decision == GateDecision.MERGE_CANDIDATE
    assert result.decision == "MERGE-CANDIDATE"
    assert result.top_match_id == "drawer-xyz"
    assert result.top_similarity == 0.87


def test_write_back_gate_store_below_merge_threshold():
    """Similarity < 0.85 → STORE even when a match exists."""
    result = write_back_gate(
        "Refactored the authentication layer to use passkeys.",
        _make_query_fn(
            {"id": "drawer-old", "similarity": 0.60, "wing": "dev", "room": "decisions"}
        ),
    )
    assert result.decision == GateDecision.STORE
    assert result.top_match_id is None
    assert result.top_similarity == 0.0


def test_write_back_gate_picks_top_similarity_when_multiple_matches():
    """With multiple matches the gate uses the highest-similarity one."""
    result = write_back_gate(
        "Decided to use ChromaDB.",
        _make_query_fn(
            {"id": "drawer-low", "similarity": 0.70},
            {"id": "drawer-high", "similarity": 0.92},
            {"id": "drawer-mid", "similarity": 0.80},
        ),
    )
    assert result.decision == GateDecision.SKIP
    assert result.top_match_id == "drawer-high"
    assert result.top_similarity == 0.92


def test_write_back_gate_scrubs_secrets_before_check():
    """Content containing a fake API key is scrubbed before the similarity query."""
    captured = []

    def query_fn(text, n_results):
        captured.append(text)
        return []

    raw = "API key: sk-ant-abc123XYZabc123XYZabc123XYZ and some real content."
    result = write_back_gate(raw, query_fn)

    assert result.decision == GateDecision.STORE
    # The text passed to query_fn must not contain the raw key
    assert len(captured) == 1
    assert "sk-ant-abc123XYZabc123XYZabc123XYZ" not in captured[0]
    assert "[REDACTED]" in captured[0]
    # The scrubbed field on the result is also clean
    assert "sk-ant-abc123XYZabc123XYZabc123XYZ" not in result.scrubbed
    assert "[REDACTED]" in result.scrubbed


def test_write_back_gate_fail_open_on_query_error():
    """When the query_fn raises, the gate returns STORE (fail-open)."""

    def bad_query_fn(text, n_results):
        raise RuntimeError("vector backend unavailable")

    result = write_back_gate("Some content.", bad_query_fn)
    assert result.decision == GateDecision.STORE
    assert result.scrubbed == "Some content."


def test_write_back_gate_invalid_thresholds():
    """merge_threshold > skip_threshold is a programming error → ValueError."""
    with pytest.raises(ValueError, match="merge_threshold"):
        write_back_gate(
            "content",
            _make_query_fn(),
            skip_threshold=0.80,
            merge_threshold=0.90,  # inverted — error
        )


def test_write_back_gate_custom_thresholds():
    """Custom thresholds are respected."""
    result = write_back_gate(
        "content",
        _make_query_fn({"id": "d1", "similarity": 0.72}),
        skip_threshold=0.80,
        merge_threshold=0.70,
    )
    # 0.72 is between 0.70 and 0.80 → MERGE-CANDIDATE under these thresholds
    assert result.decision == GateDecision.MERGE_CANDIDATE


def test_write_back_gate_exact_skip_boundary():
    """Similarity exactly at skip_threshold → SKIP."""
    result = write_back_gate(
        "content",
        _make_query_fn({"id": "d1", "similarity": 0.90}),
    )
    assert result.decision == GateDecision.SKIP


def test_write_back_gate_exact_merge_boundary():
    """Similarity exactly at merge_threshold → MERGE-CANDIDATE."""
    result = write_back_gate(
        "content",
        _make_query_fn({"id": "d1", "similarity": 0.85}),
    )
    assert result.decision == GateDecision.MERGE_CANDIDATE


# ── EpisodicRecord ────────────────────────────────────────────────────────────


def test_episodic_record_to_document_round_trip():
    """Serialise and parse an EpisodicRecord without data loss."""
    record = EpisodicRecord(
        task="Fix flaky test_miner_fts5_validation",
        what_tried="Bumped sleep from 0.05 to 0.5; added retry loop",
        what_worked="Retry loop with 3 attempts eliminated the flake",
        what_failed="Simple sleep increase was insufficient on slow CI",
    )
    doc = record.to_document()
    parsed = EpisodicRecord.from_document(doc)

    assert parsed.task == record.task
    assert parsed.what_tried == record.what_tried
    assert parsed.what_worked == record.what_worked
    assert parsed.what_failed == record.what_failed


def test_episodic_record_document_contains_all_fields():
    """The document string includes all four field labels."""
    record = EpisodicRecord(task="t", what_tried="a", what_worked="b", what_failed="c")
    doc = record.to_document()
    assert "task:" in doc
    assert "what_tried:" in doc
    assert "what_worked:" in doc
    assert "what_failed:" in doc


def test_episodic_record_from_document_missing_fields():
    """Parsing a document with missing fields returns empty strings."""
    doc = "task: only the task\n"
    parsed = EpisodicRecord.from_document(doc)
    assert parsed.task == "only the task"
    assert parsed.what_tried == ""
    assert parsed.what_worked == ""
    assert parsed.what_failed == ""


def test_episodic_record_empty_what_failed_is_valid():
    """what_failed is allowed to be empty (nothing failed)."""
    record = EpisodicRecord(
        task="Implement dedup gate",
        what_tried="One pass cosine check",
        what_worked="Matches tool_check_duplicate threshold",
        what_failed="",
    )
    doc = record.to_document()
    parsed = EpisodicRecord.from_document(doc)
    assert parsed.what_failed == ""


# ── WriteBackCategory ─────────────────────────────────────────────────────────


def test_write_back_category_values():
    """All four categories exist with correct string values."""
    assert WriteBackCategory.DECISION == "decision"
    assert WriteBackCategory.SOLVED_PROBLEM == "solved_problem"
    assert WriteBackCategory.USER_FACT == "user_fact"
    assert WriteBackCategory.SKILL == "skill"


def test_write_back_decision_defaults():
    """WriteBackDecision has sensible defaults for optional fields."""
    d = WriteBackDecision(decision=GateDecision.STORE, scrubbed="hello")
    assert d.top_match_id is None
    assert d.top_similarity == 0.0
    assert d.matches == []
    assert d.dedup_ran is True  # normal-case default


def test_write_back_gate_degraded_backend_store_flagged(caplog):
    """When query_fn raises (vector backend degraded), gate returns STORE with dedup_ran=False and emits a warning."""
    import logging

    def degraded_query_fn(text, n_results):
        raise RuntimeError("vector backend unavailable")

    with caplog.at_level(logging.WARNING, logger="sage_mcp.dedup"):
        result = write_back_gate("Some content.", degraded_query_fn)

    assert result.decision == GateDecision.STORE
    assert result.dedup_ran is False
    assert result.scrubbed == "Some content."
    # Warning must be emitted — dedup degradation must be observable
    assert any(
        "dedup" in r.message.lower() or "degraded" in r.message.lower() for r in caplog.records
    )


def test_write_back_gate_normal_store_dedup_ran_true():
    """When query_fn succeeds with no matches, dedup_ran=True."""
    result = write_back_gate("Novel content about nook architecture.", _make_query_fn())
    assert result.decision == GateDecision.STORE
    assert result.dedup_ran is True


# ── Integration: gate + episodic record + secret scrub ───────────────────────


def test_gate_with_episodic_record_novel():
    """An EpisodicRecord serialised to a document passes the gate as STORE."""
    record = EpisodicRecord(
        task="Debug HNSW seg-fault on Python 3.14",
        what_tried="Disabled multi-threaded HNSW insert (hnsw:num_threads=1)",
        what_worked="Setting the thread count to 1 eliminated the race condition",
        what_failed="",
    )
    result = write_back_gate(record.to_document(), _make_query_fn())
    assert result.decision == GateDecision.STORE


def test_gate_with_episodic_record_containing_fake_token():
    """An EpisodicRecord whose content has a fake token is scrubbed."""
    captured = []

    def query_fn(text, n_results):
        captured.append(text)
        return []

    record = EpisodicRecord(
        task="Rotate API key",
        what_tried="Updated sk-ant-FakeKeyForTestPurposesOnly123 in .env",
        what_worked="Key rotation succeeded",
        what_failed="",
    )
    result = write_back_gate(record.to_document(), query_fn)
    assert result.decision == GateDecision.STORE
    assert "sk-ant-FakeKeyForTestPurposesOnly123" not in captured[0]
    assert "[REDACTED]" in captured[0]


# ── make_query_fn — vector-disabled deterministic dedup_ran=False ─────────────


def test_make_query_fn_vector_disabled_raises():
    """make_query_fn raises VectorDisabledError when tool_check_duplicate
    returns vector_disabled: true — this lets write_back_gate set
    dedup_ran=False deterministically (WI-4 I#3 fix)."""

    def mock_check_duplicate(content, threshold):
        return {
            "is_duplicate": False,
            "matches": [],
            "vector_disabled": True,
            "vector_disabled_reason": "HNSW capacity divergence",
            "hint": "run sage repair",
        }

    fn = make_query_fn(mock_check_duplicate)
    with pytest.raises(VectorDisabledError):
        fn("some content", 5)


def test_make_query_fn_vector_disabled_gate_dedup_ran_false(caplog):
    """When make_query_fn wraps a vector-disabled backend, write_back_gate
    sets dedup_ran=False deterministically — not depending on LLM behavior."""
    import logging

    def mock_check_duplicate(content, threshold):
        return {
            "is_duplicate": False,
            "matches": [],
            "vector_disabled": True,
            "vector_disabled_reason": "backend unavailable",
        }

    fn = make_query_fn(mock_check_duplicate)
    with caplog.at_level(logging.WARNING, logger="sage_mcp.dedup"):
        result = write_back_gate("Some novel content.", fn)

    assert result.decision == GateDecision.STORE
    assert result.dedup_ran is False


def test_make_query_fn_healthy_backend_returns_matches():
    """make_query_fn returns the matches list from a healthy tool_check_duplicate result."""
    expected_matches = [{"id": "d1", "similarity": 0.91}]

    def mock_check_duplicate(content, threshold):
        return {
            "is_duplicate": True,
            "matches": expected_matches,
        }

    fn = make_query_fn(mock_check_duplicate)
    result = fn("content", 5)
    assert result == expected_matches


def test_make_query_fn_empty_matches_novel():
    """make_query_fn returns [] from a healthy result with no matches."""

    def mock_check_duplicate(content, threshold):
        return {"is_duplicate": False, "matches": []}

    fn = make_query_fn(mock_check_duplicate)
    result = fn("content", 5)
    assert result == []


def test_make_query_fn_non_dict_result_safe():
    """make_query_fn handles non-dict return without crashing."""

    def mock_check_duplicate(content, threshold):
        return None  # unexpected non-dict

    fn = make_query_fn(mock_check_duplicate)
    result = fn("content", 5)
    assert result == []
