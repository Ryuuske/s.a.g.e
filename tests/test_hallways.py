"""Tests for the within-wing hallway primitive.

Hallways are bridges INSIDE a wing that connect entities (people,
projects, concepts, interests) to each other, materialized from
drawer-level co-occurrence. Two entities are linked by a hallway when
they appear together in enough drawers across the wing.

This file is RED-first. The corresponding implementation lives in
``sage/hallways.py`` and is written to make these tests pass.
"""

from unittest.mock import MagicMock, patch


# Mock chromadb at import time so the hallways module can be loaded even
# in environments where chromadb isn't installed. Mirrors the pattern in
# ``tests/test_nook_graph_tunnels.py``.
with patch.dict("sys.modules", {"chromadb": MagicMock()}):
    from sage_mcp import hallways as hallways_mod


def _use_tmp_hallway_file(monkeypatch, tmp_path):
    """Redirect hallway persistence to a per-test JSON file."""
    hallway_file = tmp_path / "hallways.json"
    monkeypatch.setattr(hallways_mod, "_HALLWAY_FILE", str(hallway_file))
    return hallway_file


def _fake_collection(drawers):
    """Build a MagicMock collection whose .get() returns the given drawer set."""
    col = MagicMock()
    metadatas = [d for d in drawers]
    ids = [f"drawer_{i}" for i in range(len(drawers))]
    col.get.return_value = {"ids": ids, "metadatas": metadatas}
    return col


# ─────────────────────────────────────────────────────────────────────────────
# Storage primitives — _load_hallways / _save_hallways
# ─────────────────────────────────────────────────────────────────────────────


class TestHallwayStorage:
    def test_load_hallways_missing_file_returns_empty_list(self, tmp_path, monkeypatch):
        _use_tmp_hallway_file(monkeypatch, tmp_path)
        assert hallways_mod._load_hallways() == []

    def test_load_hallways_corrupt_file_returns_empty_list(self, tmp_path, monkeypatch):
        hallway_file = _use_tmp_hallway_file(monkeypatch, tmp_path)
        hallway_file.write_text("{not valid json", encoding="utf-8")
        assert hallways_mod._load_hallways() == []

    def test_save_and_load_round_trip(self, tmp_path, monkeypatch):
        _use_tmp_hallway_file(monkeypatch, tmp_path)
        sample = [
            {
                "id": "hallway_wing_alice_alice_bob_abc12345",
                "wing": "wing_alice",
                "entity_a": "Alice",
                "entity_b": "Bob",
                "co_occurrence_count": 47,
                "rooms": ["diary", "letters"],
                "label": "Alice ↔ Bob (co-occur in 47 drawers across 2 rooms)",
            }
        ]
        hallways_mod._save_hallways(sample)
        assert hallways_mod._load_hallways() == sample


# ─────────────────────────────────────────────────────────────────────────────
# compute_hallways_for_wing — entity-pair co-occurrence algorithm
# ─────────────────────────────────────────────────────────────────────────────


class TestComputeHallways:
    def test_returns_empty_for_unknown_wing(self, tmp_path, monkeypatch):
        """Wing with no drawers → no hallways, no crash."""
        _use_tmp_hallway_file(monkeypatch, tmp_path)
        col = _fake_collection([])
        result = hallways_mod.compute_hallways_for_wing("wing_nonexistent", col=col)
        assert result == []

    def test_returns_empty_when_no_drawer_has_two_entities(self, tmp_path, monkeypatch):
        """A drawer must mention >= 2 entities to contribute a pair."""
        _use_tmp_hallway_file(monkeypatch, tmp_path)
        col = _fake_collection(
            [
                {"wing": "wing_alice", "room": "diary", "entities": "Alice"},  # only one
                {"wing": "wing_alice", "room": "diary", "entities": ""},  # none
            ]
        )
        result = hallways_mod.compute_hallways_for_wing("wing_alice", col=col)
        assert result == []

    def test_creates_hallway_for_entity_pair_when_threshold_met(self, tmp_path, monkeypatch):
        """Two entities co-occurring in >= min_count drawers → one hallway record.

        With min_count=2, Alice↔Bob appear together in 3 drawers; that's a hallway.
        """
        _use_tmp_hallway_file(monkeypatch, tmp_path)
        col = _fake_collection(
            [
                {"wing": "wing_alice", "room": "diary", "entities": "Alice;Bob"},
                {"wing": "wing_alice", "room": "diary", "entities": "Alice;Bob;Ever"},
                {"wing": "wing_alice", "room": "letters", "entities": "Alice;Bob"},
            ]
        )
        result = hallways_mod.compute_hallways_for_wing("wing_alice", col=col, min_count=2)
        # Find the Alice↔Bob hallway (other pairs like Alice↔Ever might also be present)
        alice_bob = [h for h in result if {h["entity_a"], h["entity_b"]} == {"Alice", "Bob"}]
        assert len(alice_bob) == 1
        hallway = alice_bob[0]
        assert hallway["wing"] == "wing_alice"
        assert hallway["co_occurrence_count"] == 3
        assert set(hallway["rooms"]) == {"diary", "letters"}

    def test_connects_person_to_concept(self, tmp_path, monkeypatch):
        """Entities aren't only people — projects/concepts/interests count too.

        The entity tag treats 'consciousness' the same as 'Alice'; both are
        just tokens in the drawer's entities field. So Alice↔consciousness is
        a valid hallway when they co-occur enough.
        """
        _use_tmp_hallway_file(monkeypatch, tmp_path)
        col = _fake_collection(
            [
                {"wing": "wing_alice", "room": "diary", "entities": "Alice;consciousness"},
                {"wing": "wing_alice", "room": "research", "entities": "Alice;consciousness"},
                {"wing": "wing_alice", "room": "ideas", "entities": "Alice;consciousness;Bob"},
            ]
        )
        result = hallways_mod.compute_hallways_for_wing("wing_alice", col=col, min_count=2)
        alice_consciousness = [
            h for h in result if {h["entity_a"], h["entity_b"]} == {"Alice", "consciousness"}
        ]
        assert len(alice_consciousness) == 1
        assert alice_consciousness[0]["co_occurrence_count"] == 3
        assert set(alice_consciousness[0]["rooms"]) == {"diary", "research", "ideas"}

    def test_respects_min_count_threshold(self, tmp_path, monkeypatch):
        """min_count=3 filters out pairs that only co-occur twice."""
        _use_tmp_hallway_file(monkeypatch, tmp_path)
        col = _fake_collection(
            [
                {"wing": "wing_alice", "room": "diary", "entities": "Alice;Bob"},
                {"wing": "wing_alice", "room": "letters", "entities": "Alice;Bob"},
            ]
        )
        result = hallways_mod.compute_hallways_for_wing("wing_alice", col=col, min_count=3)
        assert result == []

    def test_creates_deterministic_id_per_entity_pair(self, tmp_path, monkeypatch):
        """Same wing + same entity pair → same hallway id (idempotent re-runs)."""
        _use_tmp_hallway_file(monkeypatch, tmp_path)
        col = _fake_collection(
            [
                {"wing": "wing_alice", "room": "diary", "entities": "Alice;Bob"},
                {"wing": "wing_alice", "room": "letters", "entities": "Alice;Bob"},
            ]
        )
        first = hallways_mod.compute_hallways_for_wing("wing_alice", col=col, min_count=2)
        _use_tmp_hallway_file(monkeypatch, tmp_path)
        second = hallways_mod.compute_hallways_for_wing("wing_alice", col=col, min_count=2)
        # Find the Alice↔Bob record in both runs; ids must match.
        f_id = next(h["id"] for h in first if {h["entity_a"], h["entity_b"]} == {"Alice", "Bob"})
        s_id = next(h["id"] for h in second if {h["entity_a"], h["entity_b"]} == {"Alice", "Bob"})
        assert f_id == s_id
        assert f_id.startswith("hallway_")

    def test_entity_pair_is_symmetric(self, tmp_path, monkeypatch):
        """Drawer says 'Alice;Bob'; another says 'Bob;Alice' — same hallway."""
        _use_tmp_hallway_file(monkeypatch, tmp_path)
        col = _fake_collection(
            [
                {"wing": "wing_alice", "room": "diary", "entities": "Alice;Bob"},
                {"wing": "wing_alice", "room": "letters", "entities": "Bob;Alice"},
            ]
        )
        result = hallways_mod.compute_hallways_for_wing("wing_alice", col=col, min_count=2)
        alice_bob = [h for h in result if {h["entity_a"], h["entity_b"]} == {"Alice", "Bob"}]
        # Symmetry: the two drawers count as 2 co-occurrences, not 0 (no
        # double-bookkeeping despite the swapped order).
        assert len(alice_bob) == 1
        assert alice_bob[0]["co_occurrence_count"] == 2

    def test_persists_to_json(self, tmp_path, monkeypatch):
        """After compute, _load_hallways() returns the new records."""
        hallway_file = _use_tmp_hallway_file(monkeypatch, tmp_path)
        col = _fake_collection(
            [
                {"wing": "wing_alice", "room": "diary", "entities": "Alice;Bob"},
                {"wing": "wing_alice", "room": "letters", "entities": "Alice;Bob"},
            ]
        )
        hallways_mod.compute_hallways_for_wing("wing_alice", col=col, min_count=2)
        assert hallway_file.exists()
        loaded = hallways_mod._load_hallways()
        assert any({h["entity_a"], h["entity_b"]} == {"Alice", "Bob"} for h in loaded)

    def test_tracks_rooms_across_co_occurrences(self, tmp_path, monkeypatch):
        """A hallway records the set of rooms where its entities co-occurred."""
        _use_tmp_hallway_file(monkeypatch, tmp_path)
        col = _fake_collection(
            [
                {"wing": "wing_alice", "room": "diary", "entities": "Alice;Bob"},
                {"wing": "wing_alice", "room": "letters", "entities": "Alice;Bob"},
                {"wing": "wing_alice", "room": "diary", "entities": "Alice;Bob"},
            ]
        )
        result = hallways_mod.compute_hallways_for_wing("wing_alice", col=col, min_count=2)
        h = next(h for h in result if {h["entity_a"], h["entity_b"]} == {"Alice", "Bob"})
        assert set(h["rooms"]) == {"diary", "letters"}
        assert h["co_occurrence_count"] == 3  # 3 drawers, not 3 rooms

    def test_skips_sentinel_drawers(self, tmp_path, monkeypatch):
        """Sentinels exist for file_already_mined() bookkeeping. Skip them."""
        _use_tmp_hallway_file(monkeypatch, tmp_path)
        col = _fake_collection(
            [
                {
                    "wing": "wing_alice",
                    "room": "documents",
                    "entities": "Alice;Bob",
                    "is_sentinel": True,
                },
                {
                    "wing": "wing_alice",
                    "room": "documents",
                    "entities": "Alice;Bob",
                    "is_sentinel": True,
                },
            ]
        )
        result = hallways_mod.compute_hallways_for_wing("wing_alice", col=col, min_count=2)
        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# Query API — list_hallways, delete_hallway
# ─────────────────────────────────────────────────────────────────────────────


class TestHallwayQuery:
    def test_list_hallways_returns_all_when_no_filter(self, tmp_path, monkeypatch):
        _use_tmp_hallway_file(monkeypatch, tmp_path)
        hallways_mod._save_hallways(
            [
                {"id": "h1", "wing": "wing_alice", "entity_a": "Alice", "entity_b": "Bob"},
                {"id": "h2", "wing": "wing_bob", "entity_a": "Bob", "entity_b": "Ever"},
            ]
        )
        assert len(hallways_mod.list_hallways()) == 2

    def test_list_hallways_filters_by_wing(self, tmp_path, monkeypatch):
        _use_tmp_hallway_file(monkeypatch, tmp_path)
        hallways_mod._save_hallways(
            [
                {"id": "h1", "wing": "wing_alice", "entity_a": "Alice", "entity_b": "Bob"},
                {"id": "h2", "wing": "wing_bob", "entity_a": "Bob", "entity_b": "Ever"},
            ]
        )
        result = hallways_mod.list_hallways(wing="wing_alice")
        assert len(result) == 1
        assert result[0]["id"] == "h1"

    def test_delete_hallway_removes_record(self, tmp_path, monkeypatch):
        _use_tmp_hallway_file(monkeypatch, tmp_path)
        hallways_mod._save_hallways(
            [
                {"id": "h1", "wing": "wing_alice", "entity_a": "Alice", "entity_b": "Bob"},
                {"id": "h2", "wing": "wing_alice", "entity_a": "Alice", "entity_b": "Ever"},
            ]
        )
        assert hallways_mod.delete_hallway("h1") is True
        remaining = hallways_mod._load_hallways()
        assert len(remaining) == 1
        assert remaining[0]["id"] == "h2"

    def test_delete_hallway_unknown_id_returns_false(self, tmp_path, monkeypatch):
        _use_tmp_hallway_file(monkeypatch, tmp_path)
        hallways_mod._save_hallways([{"id": "h1", "wing": "wing_alice"}])
        assert hallways_mod.delete_hallway("nonexistent") is False


# ─────────────────────────────────────────────────────────────────────────────
# L7 dynamics integration — hallway records carry strength/stability/etc
# ─────────────────────────────────────────────────────────────────────────────


class TestHallwayDynamicsIntegration:
    """Hallway records produced by ``compute_hallways_for_wing`` must carry
    the L7 dynamics fields (strength, stability, last_activated, access_count)
    so the living-connection math in ``sage.dynamics`` can operate on
    them. Plus: recomputing the same wing must PRESERVE accumulated dynamics
    rather than reset them — otherwise every mine wipes the connection
    weights and L7 is undermined."""

    def test_new_hallway_record_carries_all_dynamics_fields(self, tmp_path, monkeypatch):
        from sage_mcp.dynamics import DEFAULT_STABILITY, DEFAULT_STRENGTH

        _use_tmp_hallway_file(monkeypatch, tmp_path)
        col = _fake_collection(
            [
                {"wing": "wing_alice", "room": "diary", "entities": "Alice;Bob"},
                {"wing": "wing_alice", "room": "letters", "entities": "Alice;Bob"},
            ]
        )
        created = hallways_mod.compute_hallways_for_wing("wing_alice", col=col, min_count=2)
        assert created, "expected at least one hallway record"
        for h in created:
            assert h["strength"] == DEFAULT_STRENGTH, (
                f"new hallway should carry default strength; got {h}"
            )
            assert h["stability"] == DEFAULT_STABILITY, (
                f"new hallway should carry default stability; got {h}"
            )
            assert h["access_count"] == 0, f"new hallway should start at access_count=0; got {h}"
            assert "last_activated" in h, f"new hallway must carry last_activated; got {h}"
            # last_activated should anchor to created_at so decay starts from
            # creation, not from recompute-time.
            assert h["last_activated"] == h["created_at"]

    def test_recompute_preserves_accumulated_strength(self, tmp_path, monkeypatch):
        """If a hallway has been potentiated through use (strength > default),
        a re-run of compute_hallways_for_wing on the same drawer set must
        NOT reset that strength. Otherwise every mine wipes the connection
        weights — undermining the whole L7 dynamics layer."""
        _use_tmp_hallway_file(monkeypatch, tmp_path)
        col = _fake_collection(
            [
                {"wing": "wing_alice", "room": "diary", "entities": "Alice;Bob"},
                {"wing": "wing_alice", "room": "letters", "entities": "Alice;Bob"},
            ]
        )
        first_pass = hallways_mod.compute_hallways_for_wing("wing_alice", col=col, min_count=2)
        assert first_pass, "first pass must create at least one hallway"

        # Simulate user activity: manually bump strength + access_count on
        # the persisted records.
        stored = hallways_mod._load_hallways()
        for h in stored:
            h["strength"] = 2.5
            h["access_count"] = 7
            h["stability"] = 1.8
        hallways_mod._save_hallways(stored)

        # Recompute the same wing — should preserve the bumped values.
        hallways_mod.compute_hallways_for_wing("wing_alice", col=col, min_count=2)

        after = hallways_mod._load_hallways()
        assert after, "after-recompute records must exist"
        for h in after:
            assert h["strength"] == 2.5, (
                f"recompute reset strength — L7 dynamics undermined; got {h}"
            )
            assert h["access_count"] == 7, f"recompute reset access_count; got {h}"
            assert h["stability"] == 1.8, f"recompute reset stability; got {h}"

    def test_recompute_initializes_dynamics_for_brand_new_pairs(self, tmp_path, monkeypatch):
        """When a recompute discovers a NEW entity pair (not in the prior
        wing's hallways), the new record gets default dynamics — not
        inherited from some unrelated previous record."""
        from sage_mcp.dynamics import DEFAULT_STRENGTH

        _use_tmp_hallway_file(monkeypatch, tmp_path)
        col_a = _fake_collection(
            [
                {"wing": "wing_alice", "room": "diary", "entities": "Alice;Bob"},
                {"wing": "wing_alice", "room": "letters", "entities": "Alice;Bob"},
            ]
        )
        hallways_mod.compute_hallways_for_wing("wing_alice", col=col_a, min_count=2)

        # Bump strength on the Alice↔Bob pair to verify it's not leaked.
        stored = hallways_mod._load_hallways()
        for h in stored:
            h["strength"] = 3.5
        hallways_mod._save_hallways(stored)

        # Now a recompute with a different entity pair (Ever shows up).
        col_b = _fake_collection(
            [
                {"wing": "wing_alice", "room": "diary", "entities": "Alice;Bob"},
                {"wing": "wing_alice", "room": "letters", "entities": "Alice;Bob"},
                {"wing": "wing_alice", "room": "diary", "entities": "Alice;Ever"},
                {"wing": "wing_alice", "room": "letters", "entities": "Alice;Ever"},
            ]
        )
        hallways_mod.compute_hallways_for_wing("wing_alice", col=col_b, min_count=2)

        after = hallways_mod._load_hallways()
        alice_ever = [h for h in after if {h["entity_a"], h["entity_b"]} == {"Alice", "Ever"}]
        alice_bob = [h for h in after if {h["entity_a"], h["entity_b"]} == {"Alice", "Bob"}]

        assert len(alice_ever) == 1, "Alice↔Ever pair should now exist"
        assert alice_ever[0]["strength"] == DEFAULT_STRENGTH, (
            "new pair should get default strength, not inherit from another pair"
        )
        assert len(alice_bob) == 1
        assert alice_bob[0]["strength"] == 3.5, (
            "existing pair's accumulated strength must still be preserved"
        )

    def test_recompute_preserves_dynamics_when_existing_record_has_reversed_entity_order(
        self, tmp_path, monkeypatch
    ):
        """The dynamics-preservation lookup must canonicalize the entity-pair
        key by sorting, matching the symmetric ID generation. Otherwise a
        persisted record with (entity_a='Bob', entity_b='Alice') would miss
        the lookup when the new computation produces (entity_a='Alice',
        entity_b='Bob') — silently wiping accumulated dynamics.

        Per PR #1578 review (gemini-code-assist, HIGH priority): existing
        records may not always be stored with sorted entity order
        (manual edits, imports from other sources, alternate schema). The
        lookup must canonicalize the same way ``_hallway_id`` does.
        """
        _use_tmp_hallway_file(monkeypatch, tmp_path)

        # Pre-populate with a record whose entities are stored in REVERSED
        # (non-sorted) order, but bumped to non-default dynamics.
        hallways_mod._save_hallways(
            [
                {
                    "id": hallways_mod._hallway_id("wing_alice", "Alice", "Bob"),
                    "wing": "wing_alice",
                    "entity_a": "Bob",  # NOT sorted — Bob > Alice
                    "entity_b": "Alice",
                    "co_occurrence_count": 5,
                    "rooms": ["diary"],
                    "label": "...",
                    "created_at": "2026-04-01T00:00:00+00:00",
                    "created_by": "auto",
                    "strength": 4.2,
                    "stability": 1.9,
                    "last_activated": "2026-05-01T00:00:00+00:00",
                    "access_count": 33,
                }
            ]
        )

        # Recompute — should match the existing record via sorted-key lookup
        # and preserve its dynamics, not initialize defaults.
        col = _fake_collection(
            [
                {"wing": "wing_alice", "room": "diary", "entities": "Alice;Bob"},
                {"wing": "wing_alice", "room": "letters", "entities": "Alice;Bob"},
            ]
        )
        hallways_mod.compute_hallways_for_wing("wing_alice", col=col, min_count=2)

        after = hallways_mod._load_hallways()
        assert len(after) == 1
        assert after[0]["strength"] == 4.2, (
            "lookup failed to match the reverse-ordered existing record — "
            "strength got reset to default. Lookup key must be canonicalized "
            "by sorting the entity pair (matching _hallway_id's symmetric ID)."
        )
        assert after[0]["access_count"] == 33
        assert after[0]["stability"] == 1.9
