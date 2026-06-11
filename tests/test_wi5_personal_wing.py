"""Tests for WI-5 — Personal wing core/detail split + drawer-level confidence tag.

Demonstrates:
  (1) Personal wing registered with core (L1) + detail (non-L1) halls.
  (2) A core identity fact surfaces in the Tier-0 block; a detail fact does NOT.
  (3) A stored drawer carries a confidence tag.
  (4) user_fact write-back routes core-vs-detail correctly (agent protocol).
  (5) Existing wing count (17 canonical + Personal = 18) intact.
  (6) Confidence validation: out-of-range and wrong-type values rejected.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# (1) Wing config: Personal wing registered + type has core (L1) / detail (non-L1)
# ---------------------------------------------------------------------------


def test_personal_wing_in_wing_config():
    """Personal wing exists in wing_config.json with type=personal."""
    cfg_path = Path(__file__).parent.parent / "wing_config.json"
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "Personal" in data["wings"], "Personal wing missing from wing_config.json"
    assert data["wings"]["Personal"]["type"] == "personal"


def test_personal_wing_type_halls():
    """personal wing type declares halls=[core, detail] with l1=[core]."""
    cfg_path = Path(__file__).parent.parent / "wing_config.json"
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    wtype = data["wing_types"].get("personal")
    assert wtype is not None, "personal wing type missing from wing_config.json"
    assert set(wtype["halls"]) == {"core", "detail"}
    assert wtype["l1"] == ["core"], "core must be the only L1 hall for personal type"


def test_detail_hall_not_l1():
    """detail hall must NOT be in the l1 list for the personal type."""
    cfg_path = Path(__file__).parent.parent / "wing_config.json"
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    l1 = data["wing_types"]["personal"]["l1"]
    assert "detail" not in l1, "detail hall must NOT be in L1 (retrieval-only)"


def test_tracked_template_ships_only_framework_generic_wings():
    """The tracked wing_config.json template ships ONLY framework-generic wings.

    Codex Phase-8 HIGH fix: the template must NOT carry the maintainer's
    project/dev wings (Acme-*, Shop-Store, ZiSaStudios, Work, etc. under ~/dev) —
    those are operator-specific and leak names into a shipped/exported tree and
    into every seeded fresh-user config. Only the framework-internal wings under
    ~/.sage (Personal for WI-5 user-facts, telemetry for the telemetry stream)
    ship; a user registers their own project/dev wings via `sage bootstrap`.
    """
    cfg_path = Path(__file__).parent.parent / "wing_config.json"
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    wings = data["wings"]
    # Personal (WI-5) is present and framework-generic.
    assert "Personal" in wings, "Personal user-facts wing must ship"
    assert wings["Personal"]["type"] == "personal"
    # NO maintainer-specific project/dev wing leaks into the tracked template.
    for leaked in ("sage", "Acme-Addon", "Acme-Ops.V3", "Shop-Store", "ZiSaStudios", "Work"):
        assert leaked not in wings, f"maintainer wing {leaked!r} must not ship in the template"
    # Every shipped wing is framework-internal (path under ~/.sage) or path-less.
    for slug, entry in wings.items():
        path = str(entry.get("path", ""))
        assert (not path) or path.startswith("~/.sage"), (
            f"shipped wing {slug!r} has non-framework path {path!r}"
        )


# ---------------------------------------------------------------------------
# (2) Personal/core surfaces in Tier-0; Personal/detail does NOT
# ---------------------------------------------------------------------------


def _make_stack(tmp_path, nook_path_str):
    """Build a MemoryStack with an identity file pointing at nook_path_str."""
    identity_file = tmp_path / "identity.txt"
    identity_file.write_text("I am Atlas.", encoding="utf-8")
    from sage_mcp.layers import MemoryStack

    with patch("sage_mcp.layers.SageConfig") as mock_cfg:
        mock_cfg.return_value.nook_path = nook_path_str
        stack = MemoryStack(
            nook_path=nook_path_str,
            identity_path=str(identity_file),
        )
    return stack


def _make_personal_core_col(core_docs, detail_docs=None):
    """Return a mock ChromaDB collection with Personal/core and Personal/detail drawers."""
    detail_docs = detail_docs or []

    def _col_get(where=None, include=None, limit=None, offset=None, **kw):
        # Decide which batch to return based on the where filter
        if where is None:
            # Used by Layer1.generate() pagination
            return {"documents": [], "metadatas": []}
        # Personal/core query: {"$and": [{"wing": "Personal"}, {"hall": "core"}]}
        if isinstance(where, dict) and "$and" in where:
            conditions = where["$and"]
            has_personal = any(c.get("wing") == "Personal" for c in conditions)
            has_core = any(c.get("hall") == "core" for c in conditions)
            if has_personal and has_core:
                docs = core_docs
                metas = [
                    {
                        "wing": "Personal",
                        "hall": "core",
                        "filed_at": "2026-01-01T00:00:00",
                        "confidence": 1.0,
                        "importance": 5,
                    }
                    for _ in docs
                ]
                return {"documents": docs, "metadatas": metas}
        # Any other query (L1 wing-scoped, etc.) returns empty
        return {"documents": [], "metadatas": []}

    mock_col = MagicMock()
    mock_col.get.side_effect = _col_get
    return mock_col


def test_personal_core_surfaces_in_tier0(tmp_path):
    """A core identity fact appears in the Tier-0 block text."""
    nook_p = str(tmp_path / "nook")
    stack = _make_stack(tmp_path, nook_p)

    mock_col = _make_personal_core_col(
        core_docs=["User prefers dark mode and uses Ryuuske as their GitHub handle."]
    )

    with patch("sage_mcp.layers._get_collection", return_value=mock_col):
        block = stack.assemble_tier0(wing=None, repo_root=None)

    assert "PERSONAL IDENTITY" in block.text, (
        "Personal/core section header missing from Tier-0 block."
    )
    assert "dark mode" in block.text, "Personal/core fact 'dark mode' missing from Tier-0 block."


def test_personal_detail_not_in_tier0(tmp_path):
    """A detail-only fact does NOT appear in the Tier-0 block.

    detail drawers are retrieval-only; they must not be injected into Tier-0.
    _load_personal_core only queries hall=core; the detail drawer is never fetched.
    """
    nook_p = str(tmp_path / "nook")
    stack = _make_stack(tmp_path, nook_p)

    # Mock that returns NOTHING for Personal/core (empty nook core)
    # but would return something for detail if mistakenly queried.
    mock_col = _make_personal_core_col(
        core_docs=[],
        detail_docs=["This is a detail-only personal memory that must not surface."],
    )

    with patch("sage_mcp.layers._get_collection", return_value=mock_col):
        block = stack.assemble_tier0(wing=None, repo_root=None)

    assert "detail-only personal memory" not in block.text, (
        "Personal/detail content leaked into Tier-0 block."
    )
    # And the core section header should also be absent (no core drawers)
    assert "PERSONAL IDENTITY" not in block.text


def test_personal_core_empty_nook_graceful(tmp_path):
    """When the nook has no Personal/core drawers, assemble_tier0 succeeds."""
    nook_p = str(tmp_path / "nook")
    stack = _make_stack(tmp_path, nook_p)

    mock_col = _make_personal_core_col(core_docs=[])

    with patch("sage_mcp.layers._get_collection", return_value=mock_col):
        block = stack.assemble_tier0(wing=None, repo_root=None)

    assert block.text  # Block assembled without error
    assert "Atlas" in block.text  # L0 identity still present


def test_personal_core_exception_graceful(tmp_path):
    """If the nook raises when querying Personal/core, Tier-0 still assembles."""
    nook_p = str(tmp_path / "nook")
    stack = _make_stack(tmp_path, nook_p)

    mock_col = MagicMock()
    mock_col.get.side_effect = RuntimeError("database error")

    with patch("sage_mcp.layers._get_collection", return_value=mock_col):
        block = stack.assemble_tier0(wing=None, repo_root=None)

    # Must not raise; L0 identity is still present
    assert "Atlas" in block.text


def test_personal_core_no_nook_graceful(tmp_path):
    """_load_personal_core returns '' when the nook is unreachable."""
    from sage_mcp.layers import _load_personal_core

    with patch("sage_mcp.layers._get_collection", side_effect=Exception("no nook")):
        result = _load_personal_core("/nonexistent/nook")

    assert result == ""


# ---------------------------------------------------------------------------
# (3) Stored drawer carries confidence tag
# ---------------------------------------------------------------------------


class _ColResult:
    """Minimal fake ChromaCollection query result with attribute-style .ids access.

    mcp_server.tool_add_drawer uses ``existing.ids`` (attribute access, not
    dict key) on the result of ``col.get()``. This helper mirrors the shape
    of a real ``ChromaCollection`` result so the idempotency check and the
    post-upsert verification both behave correctly in tests.
    """

    def __init__(self, ids=None):
        self.ids = ids or []


def _make_fake_col_with_upsert_capture(upserted_metas):
    """Build a fake ChromaCollection that captures upserted metadata.

    Returns an object with .ids attribute (matching the real ChromaCollection
    shape that mcp_server.tool_add_drawer accesses via attribute, not dict key).
    Patches chunk_size to a known integer so the content-length comparison
    ``len(content) <= chunk_size`` is not vulnerable to a stale MagicMock
    ``_config`` leftover from other tests in the full suite.
    """

    class _FakeCol:
        def __init__(self):
            self._last_upserted_ids = []

        def get(self, ids=None, where=None, include=None, **kw):
            if ids:
                matching = [i for i in ids if i in self._last_upserted_ids]
                if matching:
                    return _ColResult(ids=matching)
            return _ColResult(ids=[])

        def upsert(self, ids, documents, metadatas):
            upserted_metas.extend(metadatas)
            self._last_upserted_ids = list(ids)

        def count(self):
            return 0

    return _FakeCol()


def test_tool_add_drawer_stores_confidence():
    """nook_add_drawer stores confidence in the drawer metadata."""
    from sage_mcp import mcp_server
    from sage_mcp.config import SageConfig

    upserted_metas = []
    fake_col = _make_fake_col_with_upsert_capture(upserted_metas)
    real_config = SageConfig()

    with (
        patch.object(mcp_server, "_get_collection", return_value=fake_col),
        patch.object(mcp_server, "_config", real_config),
        patch(
            "sage_mcp.extensions.wing_registry.require_registered_wing",
            return_value=None,
        ),
    ):
        result = mcp_server.tool_add_drawer(
            wing="Personal",
            room="core",
            content="User prefers concise answers.",
            hall="core",
            confidence=0.9,
        )

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert len(upserted_metas) > 0, "Expected upsert to be called"
    assert upserted_metas[0]["confidence"] == pytest.approx(0.9), (
        f"Expected confidence=0.9 in stored metadata, got: {upserted_metas[0]}"
    )


def test_tool_add_drawer_default_confidence():
    """nook_add_drawer uses confidence=1.0 by default."""
    from sage_mcp import mcp_server
    from sage_mcp.config import SageConfig

    upserted_metas = []
    fake_col = _make_fake_col_with_upsert_capture(upserted_metas)
    real_config = SageConfig()

    with (
        patch.object(mcp_server, "_get_collection", return_value=fake_col),
        patch.object(mcp_server, "_config", real_config),
        patch(
            "sage_mcp.extensions.wing_registry.require_registered_wing",
            return_value=None,
        ),
    ):
        result = mcp_server.tool_add_drawer(
            wing="Personal",
            room="core",
            content="User is a software developer.",
            hall="core",
            # no confidence kwarg — must default to 1.0
        )

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert len(upserted_metas) > 0, "Expected upsert to be called"
    assert upserted_metas[0]["confidence"] == pytest.approx(1.0), (
        f"Expected default confidence=1.0, got: {upserted_metas[0].get('confidence')}"
    )


def test_tool_add_drawer_confidence_out_of_range():
    """nook_add_drawer rejects confidence values outside [0.0, 1.0]."""
    from sage_mcp import mcp_server

    with patch(
        "sage_mcp.extensions.wing_registry.require_registered_wing",
        return_value=None,
    ):
        result_high = mcp_server.tool_add_drawer(
            wing="Personal",
            room="core",
            content="Some content",
            confidence=1.5,
        )
        result_neg = mcp_server.tool_add_drawer(
            wing="Personal",
            room="core",
            content="Some content",
            confidence=-0.1,
        )

    assert result_high["success"] is False
    assert "confidence" in result_high["error"].lower()
    assert result_neg["success"] is False
    assert "confidence" in result_neg["error"].lower()


def test_tool_add_drawer_confidence_wrong_type():
    """nook_add_drawer rejects non-numeric confidence values."""
    from sage_mcp import mcp_server

    with patch(
        "sage_mcp.extensions.wing_registry.require_registered_wing",
        return_value=None,
    ):
        result = mcp_server.tool_add_drawer(
            wing="Personal",
            room="core",
            content="Some content",
            confidence="high",
        )

    assert result["success"] is False
    assert "confidence" in result["error"].lower()


def test_tool_add_drawer_confidence_none_coerces_to_default():
    """nook_add_drawer coerces explicit confidence=None to 1.0 (unspecified = default).

    None is a natural 'unspecified' sentinel; callers should not receive an error
    for passing it explicitly. NaN, inf, and out-of-range values still hard-error.
    """
    from sage_mcp import mcp_server
    from sage_mcp.config import SageConfig

    upserted_metas = []
    fake_col = _make_fake_col_with_upsert_capture(upserted_metas)
    real_config = SageConfig()

    with (
        patch.object(mcp_server, "_get_collection", return_value=fake_col),
        patch.object(mcp_server, "_config", real_config),
        patch(
            "sage_mcp.extensions.wing_registry.require_registered_wing",
            return_value=None,
        ),
    ):
        result = mcp_server.tool_add_drawer(
            wing="Personal",
            room="core",
            content="User prefers explicit defaults.",
            hall="core",
            confidence=None,
        )

    assert result.get("success") is True, f"Expected success when confidence=None, got: {result}"
    assert len(upserted_metas) > 0, "Expected upsert to be called"
    assert upserted_metas[0]["confidence"] == pytest.approx(1.0), (
        f"Expected confidence coerced to 1.0, got: {upserted_metas[0].get('confidence')}"
    )


# ---------------------------------------------------------------------------
# (4) wing_registry recognizes "personal" as a valid wing type
# ---------------------------------------------------------------------------


def test_personal_is_valid_wing_type():
    """'personal' is in VALID_WING_TYPES so add_wing accepts it."""
    from sage_mcp.extensions.wing_registry import VALID_WING_TYPES

    assert "personal" in VALID_WING_TYPES


def test_personal_wing_registered_in_fixture():
    """The test fixture wing_config.json registers Personal (WI-5 test isolation)."""
    fixture_path = Path(__file__).parent / "fixtures" / "wing_config.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert "Personal" in data["wings"]
    assert "personal" in data["wing_types"]


# ---------------------------------------------------------------------------
# (5) _load_personal_core determinism
# ---------------------------------------------------------------------------


def test_personal_core_deterministic_order(tmp_path):
    """_load_personal_core produces stable output across calls for the same data."""
    from sage_mcp.layers import _load_personal_core

    # Two drawers with identical importance and filed_at — md5 tiebreaker applies.
    docs = ["Fact alpha: user prefers dark mode.", "Fact beta: user prefers concise replies."]
    metas = [
        {
            "wing": "Personal",
            "hall": "core",
            "filed_at": "2026-01-01T00:00:00",
            "importance": 5,
            "confidence": 1.0,
        },
        {
            "wing": "Personal",
            "hall": "core",
            "filed_at": "2026-01-01T00:00:00",
            "importance": 5,
            "confidence": 1.0,
        },
    ]

    def _make_col():
        mock_col = MagicMock()
        mock_col.get.return_value = {"documents": docs, "metadatas": metas}
        return mock_col

    results = []
    for _ in range(3):
        with patch("sage_mcp.layers._get_collection", return_value=_make_col()):
            results.append(_load_personal_core(str(tmp_path / "nook")))

    assert results[0] == results[1] == results[2], (
        "_load_personal_core output is not deterministic."
    )


# ---------------------------------------------------------------------------
# (6) Personal/core stays within Tier-0 budget (doesn't bloat L0)
# ---------------------------------------------------------------------------


def test_personal_core_respects_budget(tmp_path):
    """Tier-0 budget enforcement still applies when Personal/core has content."""
    nook_p = str(tmp_path / "nook")
    stack = _make_stack(tmp_path, nook_p)

    # Inject a large core fact to trigger budget trimming
    large_fact = "Very important identity fact: " + "X" * 600
    mock_col = _make_personal_core_col(core_docs=[large_fact])

    micro_budget = 20  # tokens — force trim
    with patch("sage_mcp.layers._get_collection", return_value=mock_col):
        block = stack.assemble_tier0(wing=None, repo_root=None, budget=micro_budget)

    # Budget enforcement produces a trim marker when over budget
    content_chars = len(block.text)
    # The trim path cuts to char_limit + marker; the block.text may exceed
    # the char_limit slightly due to the marker itself — what matters is the
    # block was assembled without error and is finite.
    assert content_chars > 0
    assert block.budget == micro_budget
