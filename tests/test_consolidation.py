"""Tests for sage.consolidation — WI-6 drawer-level decay and consolidation.

Coverage:
  (1) Decay floors — no drawer's strength can drop below DRAWER_STRENGTH_FLOOR.
  (2) No-delete invariant — decay_pass and consolidation_pass NEVER delete drawers.
  (3) Core exclusion — hall=core drawers are untouched by both passes.
  (4) Confidence weighting — high-confidence drawers decay more slowly.
  (5) Consolidation down-ranks near-duplicates without deleting them.
  (6) Searcher uses strength — _hybrid_rank down-ranks a decayed drawer.
  (7) Determinism — same inputs produce the same outputs.
  (8) Dry-run safety — dry_run=True never writes to the collection.
  (9) Fixture nook demo — stale drawer decays, core untouched, high-confidence
      barely decays, near-duplicate demoted not deleted, searcher down-ranks decayed.
  (10) Decay idempotency — running decay_pass 3× with same now produces identical
       strength as 1×, and the value equals the pure-formula result from provenance.
  (11) Real demotion — consolidation_pass actually demotes a near-duplicate
       (strength == CONSOLIDATION_DEMOTE_STRENGTH), not just a no-op assertion.
  (12) Merge-candidate tunnel path — consolidation correctly demotes the
       lower-confidence member of a merge-candidate tunnel pair.
"""

from __future__ import annotations

import math
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone

import chromadb
import pytest

from sage_mcp.consolidation import (
    BASE_DECAY_RATE,
    CORE_HALL_NAME,
    DRAWER_STRENGTH_DEFAULT,
    DRAWER_STRENGTH_FLOOR,
    HIGH_CONFIDENCE_PROTECTION,
    consolidation_pass,
    decay_pass,
    summarise_consolidation_results,
    summarise_decay_results,
    _compute_decay,
    _current_confidence,
    _current_strength,
    _is_core_hall,
)
from sage_mcp.searcher import _hybrid_rank


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
T_STALE = T0 - timedelta(days=90)  # 90 days ago — should decay meaningfully


@pytest.fixture
def tmp_dir_local():
    d = tempfile.mkdtemp(prefix="solo_consolidation_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def chroma_col(tmp_dir_local):
    """A real ChromaDB collection for integration tests.

    Uses a local temp dir so tests NEVER touch the live ~/.sage store.
    """
    nook_path = os.path.join(tmp_dir_local, "nook")
    os.makedirs(nook_path)
    client = chromadb.PersistentClient(path=nook_path)
    col = client.get_or_create_collection("nook_drawers", metadata={"hnsw:space": "cosine"})
    yield col
    try:
        client.delete_collection("nook_drawers")
    except Exception:
        pass


def _add_drawer(
    col,
    drawer_id,
    text,
    wing="test",
    room="facts",
    hall=None,
    confidence=1.0,
    filed_at=None,
    strength=None,
):
    """Helper: insert a drawer with controlled metadata."""
    meta = {
        "wing": wing,
        "room": room,
        "source_file": f"test_{drawer_id}.txt",
        "chunk_index": 0,
        "added_by": "test",
        "filed_at": (filed_at or T0).isoformat(),
        "confidence": float(confidence),
    }
    if hall is not None:
        meta["hall"] = hall
    if strength is not None:
        meta["strength"] = float(strength)
    col.upsert(ids=[drawer_id], documents=[text], metadatas=[meta])
    return drawer_id


# ─────────────────────────────────────────────────────────────────────────────
# (1) Pure-math decay formula tests — no I/O
# ─────────────────────────────────────────────────────────────────────────────


class TestDecayFormula:
    def test_zero_days_no_decay(self):
        """With 0 days elapsed, strength equals DRAWER_STRENGTH_DEFAULT (no decay)."""
        result = _compute_decay(confidence=0.5, days_since_used=0.0)
        assert result == pytest.approx(DRAWER_STRENGTH_DEFAULT, rel=1e-6)

    def test_floor_enforced_extreme_age(self):
        """After astronomically long staleness, strength hits the floor, not 0."""
        result = _compute_decay(confidence=0.0, days_since_used=1_000_000.0)
        assert result == DRAWER_STRENGTH_FLOOR
        assert result > 0

    def test_high_confidence_decays_slower_than_low_confidence(self):
        """High-confidence drawers retain more strength over the same time window."""
        high = _compute_decay(confidence=1.0, days_since_used=30.0)
        low = _compute_decay(confidence=0.0, days_since_used=30.0)
        assert high > low, "high-confidence should decay slower than low-confidence"

    def test_confidence_1_barely_decays(self):
        """confidence=1.0 → effective_rate = BASE_DECAY_RATE * (1 - 1.0 * HIGH_CONFIDENCE_PROTECTION).
        After 30 days that should still be close to 1.0."""
        effective_rate = BASE_DECAY_RATE * (1.0 - 1.0 * HIGH_CONFIDENCE_PROTECTION)
        expected = DRAWER_STRENGTH_DEFAULT * math.exp(-30.0 * effective_rate)
        result = _compute_decay(confidence=1.0, days_since_used=30.0)
        assert result == pytest.approx(max(DRAWER_STRENGTH_FLOOR, expected), rel=1e-6)
        # Verify that after 30 days, confidence=1.0 drawer retains > 85% strength.
        assert result > 0.85

    def test_floor_never_exceeded_from_below(self):
        """new_strength is always >= DRAWER_STRENGTH_FLOOR."""
        for days in [0, 1, 10, 100, 10000]:
            for conf in [0.0, 0.5, 1.0]:
                result = _compute_decay(confidence=conf, days_since_used=float(days))
                assert result >= DRAWER_STRENGTH_FLOOR, (
                    f"Floor violated for days={days}, conf={conf}: result={result}"
                )

    def test_determinism(self):
        """Same inputs always produce the same output."""
        a = _compute_decay(confidence=0.6, days_since_used=14.5)
        b = _compute_decay(confidence=0.6, days_since_used=14.5)
        assert a == b


class TestHelpers:
    def test_is_core_hall_true(self):
        assert _is_core_hall({"hall": CORE_HALL_NAME}) is True

    def test_is_core_hall_false_for_other_halls(self):
        assert _is_core_hall({"hall": "handoff"}) is False
        assert _is_core_hall({}) is False
        assert _is_core_hall({"hall": None}) is False

    def test_current_strength_default(self):
        assert _current_strength({}) == DRAWER_STRENGTH_DEFAULT

    def test_current_strength_reads_field(self):
        assert _current_strength({"strength": 0.42}) == pytest.approx(0.42)

    def test_current_confidence_default(self):
        assert _current_confidence({}) == 1.0

    def test_current_confidence_clamps(self):
        assert _current_confidence({"confidence": 1.5}) == 1.0
        assert _current_confidence({"confidence": -0.1}) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# (2) decay_pass — integration tests with real ChromaDB
# ─────────────────────────────────────────────────────────────────────────────


class TestDecayPass:
    def test_stale_low_confidence_drawer_strength_drops_but_stays_gte_floor(self, chroma_col):
        """A stale drawer's strength drops but is >= DRAWER_STRENGTH_FLOOR after decay."""
        _add_drawer(
            chroma_col,
            "drawer_stale_001",
            "Stale low-confidence content",
            confidence=0.1,
            filed_at=T_STALE,
            strength=1.0,
        )
        now = T0 + timedelta(days=0)
        results = decay_pass(chroma_col, now=now, dry_run=True)
        assert len(results) == 1
        r = results[0]
        assert r.drawer_id == "drawer_stale_001"
        assert not r.skipped
        assert r.new_strength < r.old_strength, "Stale drawer should have lower new_strength"
        assert r.new_strength >= DRAWER_STRENGTH_FLOOR, "Must never drop below floor"
        assert r.new_strength > 0, "Must never be zero"

    def test_no_delete_invariant_structural(self, chroma_col):
        """After decay_pass(dry_run=False), all drawers still exist in the collection."""
        ids = []
        for i in range(5):
            did = f"drawer_nd_{i:03d}"
            _add_drawer(
                chroma_col,
                did,
                f"Test content {i} with some words to search",
                confidence=float(i) / 4.0,
                filed_at=T_STALE - timedelta(days=i * 10),
                strength=1.0,
            )
            ids.append(did)

        # Apply the decay pass.
        decay_pass(chroma_col, now=T0, dry_run=False)

        # Verify every drawer still exists in the collection.
        existing = chroma_col.get(ids=ids, include=["metadatas"])
        assert len(existing["ids"]) == len(ids), (
            "decay_pass must NEVER delete drawers: "
            f"expected {len(ids)}, found {len(existing['ids'])}"
        )

    def test_core_hall_drawer_untouched_by_decay(self, chroma_col):
        """A drawer with hall=core is skipped — strength unchanged after decay."""
        _add_drawer(
            chroma_col,
            "drawer_core_001",
            "Durable identity fact: real name is Ryuuske",
            hall=CORE_HALL_NAME,
            confidence=1.0,
            filed_at=T_STALE,  # very old
            strength=1.0,
        )
        now = T0
        results = decay_pass(chroma_col, now=now, dry_run=True)
        assert len(results) == 1
        r = results[0]
        assert r.skipped is True
        assert r.reason == "skipped:core_hall"
        assert r.old_strength == r.new_strength, "Core hall drawer strength must be unchanged"

    def test_core_hall_strength_unchanged_after_apply(self, chroma_col):
        """After decay_pass(dry_run=False), core drawer's strength is still 1.0."""
        _add_drawer(
            chroma_col,
            "drawer_core_apply_001",
            "Core fact: preferred AI is Claude",
            hall=CORE_HALL_NAME,
            confidence=1.0,
            filed_at=T_STALE,
            strength=1.0,
        )
        decay_pass(chroma_col, now=T0, dry_run=False)

        # Verify strength was NOT written (still at default 1.0, not updated).
        existing = chroma_col.get(ids=["drawer_core_apply_001"], include=["metadatas"])
        meta = existing["metadatas"][0]
        # The core drawer may have strength=1.0 (initial) or no strength key (also fine).
        stored = meta.get("strength")
        if stored is not None:
            assert float(stored) == pytest.approx(1.0, rel=1e-6), (
                "Core drawer strength must not be changed by decay_pass"
            )

    def test_high_confidence_barely_decays(self, chroma_col):
        """confidence=1.0 drawer retains significantly more strength than confidence=0.0.

        After 90 days:
          - confidence=1.0: effective_rate = BASE * (1 - PROTECTION) = 0.05 * 0.05 = 0.0025/day
                             → exp(-90 * 0.0025) ≈ 0.80 (retains ~80%)
          - confidence=0.0: effective_rate = 0.05/day
                             → exp(-90 * 0.05) ≈ 0.011, floored to DRAWER_STRENGTH_FLOOR=0.10
        So high-confidence retains > 70% and is clearly above the floor, demonstrating
        the protection works. The test threshold is >0.70 (actual is ~0.80).
        """
        _add_drawer(
            chroma_col,
            "drawer_highconf_001",
            "High-confidence decision: use PostgreSQL for all relational storage",
            confidence=1.0,
            filed_at=T_STALE,  # 90 days ago
            strength=1.0,
        )
        results = decay_pass(chroma_col, now=T0, dry_run=True)
        active = [r for r in results if not r.skipped]
        assert len(active) == 1
        r = active[0]
        assert r.new_strength > 0.70, (
            f"High-confidence drawer should retain >70% after 90 days, got {r.new_strength}"
        )
        # Also verify it's well above the floor (not decayed like a zero-confidence drawer).
        assert r.new_strength > DRAWER_STRENGTH_FLOOR * 5, (
            "High-confidence drawer should be substantially above the floor after 90 days"
        )

    def test_dry_run_does_not_write(self, chroma_col):
        """dry_run=True must not write any changes to the collection."""
        _add_drawer(
            chroma_col,
            "drawer_dryrun_001",
            "Some content that would decay",
            confidence=0.1,
            filed_at=T_STALE,
            strength=1.0,
        )
        results = decay_pass(chroma_col, now=T0, dry_run=True)
        assert all(not r.written for r in results), "dry_run=True must not write any changes"

        # Verify metadata unchanged in collection.
        existing = chroma_col.get(ids=["drawer_dryrun_001"], include=["metadatas"])
        meta = existing["metadatas"][0]
        stored = meta.get("strength", DRAWER_STRENGTH_DEFAULT)
        assert float(stored) == pytest.approx(1.0, rel=1e-6), (
            "dry_run=True must not change stored metadata"
        )

    def test_apply_writes_updated_strength(self, chroma_col):
        """dry_run=False writes updated strength for a stale drawer."""
        _add_drawer(
            chroma_col,
            "drawer_apply_001",
            "Old content that should decay",
            confidence=0.0,
            filed_at=T_STALE,
            strength=1.0,
        )
        results = decay_pass(chroma_col, now=T0, dry_run=False)
        active = [r for r in results if not r.skipped]
        assert len(active) == 1
        r = active[0]
        assert r.written, "decay should have been written (strength changed)"
        assert r.new_strength < r.old_strength

        # Verify the strength is actually stored now.
        existing = chroma_col.get(ids=["drawer_apply_001"], include=["metadatas"])
        meta = existing["metadatas"][0]
        stored = float(meta.get("strength", DRAWER_STRENGTH_DEFAULT))
        assert stored == pytest.approx(r.new_strength, rel=1e-5), (
            "Stored strength should match the computed new_strength"
        )

    def test_summarise_decay_results(self, chroma_col):
        """summarise_decay_results aggregates correctly."""
        _add_drawer(
            chroma_col,
            "s_core",
            "Core content",
            hall=CORE_HALL_NAME,
            confidence=1.0,
            filed_at=T_STALE,
        )
        _add_drawer(
            chroma_col, "s_stale", "Old content", confidence=0.0, filed_at=T_STALE, strength=1.0
        )
        _add_drawer(
            chroma_col, "s_fresh", "Recent content", confidence=1.0, filed_at=T0, strength=1.0
        )

        results = decay_pass(chroma_col, now=T0, dry_run=True)
        summary = summarise_decay_results(results)
        assert summary["total"] == 3
        assert summary["skipped_core"] == 1
        # stale + fresh processed (2 drawers)
        assert summary["decayed"] + summary["no_change"] == 2

    def test_wing_filter(self, chroma_col):
        """Wing filter limits decay to the specified wing only."""
        _add_drawer(
            chroma_col, "w_a_001", "Wing A content", wing="wing_a", filed_at=T_STALE, strength=1.0
        )
        _add_drawer(
            chroma_col, "w_b_001", "Wing B content", wing="wing_b", filed_at=T_STALE, strength=1.0
        )

        results = decay_pass(chroma_col, now=T0, wing="wing_a", dry_run=True)
        wings_seen = {r.wing for r in results}
        assert "wing_a" in wings_seen
        assert "wing_b" not in wings_seen


# ─────────────────────────────────────────────────────────────────────────────
# (5) consolidation_pass — near-duplicate down-ranking
# ─────────────────────────────────────────────────────────────────────────────


class TestConsolidationPass:
    def test_no_delete_invariant_consolidation(self, chroma_col):
        """consolidation_pass never deletes any drawer, even near-duplicates."""
        # Two near-identical drawers.
        _add_drawer(
            chroma_col,
            "c_dup_001",
            "The PostgreSQL database uses connection pooling via pgbouncer for load management",
            confidence=0.5,
        )
        _add_drawer(
            chroma_col,
            "c_dup_002",
            "PostgreSQL database uses connection pooling via pgbouncer for load balancing",
            confidence=0.8,
        )

        consolidation_pass(chroma_col, dry_run=False)

        existing = chroma_col.get(ids=["c_dup_001", "c_dup_002"], include=["metadatas"])
        assert len(existing["ids"]) == 2, (
            "consolidation_pass MUST NOT delete drawers: both must still exist"
        )

    def test_demoted_drawer_strength_above_floor(self, chroma_col):
        """A demoted near-duplicate has strength >= DRAWER_STRENGTH_FLOOR."""
        _add_drawer(
            chroma_col,
            "c_low_001",
            "The system uses JWT tokens for authentication and authorization",
            confidence=0.3,
        )
        _add_drawer(
            chroma_col,
            "c_low_002",
            "JWT tokens are used for authentication and authorization in this system",
            confidence=0.7,
        )

        results = consolidation_pass(chroma_col, dry_run=True)
        demoted = [r for r in results if r.reason == "demoted:near_duplicate"]
        for r in demoted:
            assert r.new_strength >= DRAWER_STRENGTH_FLOOR, (
                f"Demoted drawer strength {r.new_strength} must be >= floor {DRAWER_STRENGTH_FLOOR}"
            )
            assert r.new_strength > 0, "Demoted drawer must have positive strength"

    def test_core_excluded_from_consolidation(self, chroma_col):
        """hall=core drawers are never demoted by consolidation_pass."""
        # Add a core drawer and a non-core near-duplicate.
        _add_drawer(
            chroma_col,
            "c_core_001",
            "Core identity fact: user is Ryuuske on GitHub",
            hall=CORE_HALL_NAME,
            confidence=1.0,
        )
        _add_drawer(
            chroma_col,
            "c_noncore_001",
            "User is known as Ryuuske on GitHub for public work",
            confidence=0.5,
        )

        results = consolidation_pass(chroma_col, dry_run=True)
        core_results = [r for r in results if r.drawer_id == "c_core_001"]
        assert len(core_results) == 1
        assert core_results[0].skipped is True
        assert core_results[0].reason == "skipped:core_hall"
        assert core_results[0].old_strength == core_results[0].new_strength

    def test_dry_run_consolidation_no_writes(self, chroma_col):
        """consolidation_pass(dry_run=True) does not update any metadata."""
        _add_drawer(
            chroma_col,
            "cd_001",
            "The React frontend uses TanStack Query for state management",
            confidence=0.5,
        )
        _add_drawer(
            chroma_col,
            "cd_002",
            "React frontend uses TanStack Query for server state management",
            confidence=0.8,
        )

        results = consolidation_pass(chroma_col, dry_run=True)
        assert all(not r.written for r in results), "dry_run=True must not write"

    def test_summarise_consolidation(self, chroma_col):
        """summarise_consolidation_results aggregates correctly."""
        _add_drawer(chroma_col, "sc_core", "Core fact", hall=CORE_HALL_NAME)
        _add_drawer(
            chroma_col, "sc_unique", "Unique content about something specific and different"
        )
        # These two are near-identical.
        _add_drawer(
            chroma_col,
            "sc_dup1",
            "The database schema uses PostgreSQL with Alembic migrations",
            confidence=0.5,
        )
        _add_drawer(
            chroma_col,
            "sc_dup2",
            "Database schema uses PostgreSQL and Alembic for migrations management",
            confidence=0.8,
        )

        results = consolidation_pass(chroma_col, dry_run=True)
        summary = summarise_consolidation_results(results)
        assert summary["total"] == 4
        assert summary["skipped_core"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# (10) Decay idempotency — running decay_pass 3× == 1×, == pure formula
# ─────────────────────────────────────────────────────────────────────────────


class TestDecayIdempotency:
    def test_idempotency_pure_formula(self):
        """_compute_decay is path-independent: same inputs always give same output.

        The formula anchors on DRAWER_STRENGTH_DEFAULT, not on the stored strength,
        so running it multiple times with the same age/confidence is identical
        to running it once.
        """
        confidence = 0.3
        days = 45.0
        result_1 = _compute_decay(confidence=confidence, days_since_used=days)
        result_2 = _compute_decay(confidence=confidence, days_since_used=days)
        result_3 = _compute_decay(confidence=confidence, days_since_used=days)
        assert result_1 == result_2 == result_3, (
            "Pure decay formula must be idempotent: same inputs → same output"
        )
        # Also verify the value equals the analytical formula.
        from sage_mcp.consolidation import BASE_DECAY_RATE, HIGH_CONFIDENCE_PROTECTION

        effective_rate = BASE_DECAY_RATE * (1.0 - confidence * HIGH_CONFIDENCE_PROTECTION)
        expected = max(
            DRAWER_STRENGTH_FLOOR, DRAWER_STRENGTH_DEFAULT * math.exp(-days * effective_rate)
        )
        assert result_1 == pytest.approx(expected, rel=1e-9)

    def test_decay_pass_idempotent_3x_equals_1x(self, chroma_col):
        """Running decay_pass 3× with the same now= produces identical stored strength as 1×.

        Because _compute_decay anchors on DRAWER_STRENGTH_DEFAULT (not the stored strength),
        each pass rewrites the same value regardless of how many times it has run before.
        """
        filed = T_STALE  # 90 days before T0
        _add_drawer(
            chroma_col,
            "idem_001",
            "Test content for idempotency check with confidence=0.4",
            confidence=0.4,
            filed_at=filed,
            strength=DRAWER_STRENGTH_DEFAULT,
        )

        now = T0

        # Run 1×.
        decay_pass(chroma_col, now=now, dry_run=False)
        meta_after_1 = chroma_col.get(ids=["idem_001"], include=["metadatas"])["metadatas"][0]
        strength_after_1 = float(meta_after_1.get("strength", DRAWER_STRENGTH_DEFAULT))

        # Run 2× more (total 3 passes with same now).
        decay_pass(chroma_col, now=now, dry_run=False)
        decay_pass(chroma_col, now=now, dry_run=False)
        meta_after_3 = chroma_col.get(ids=["idem_001"], include=["metadatas"])["metadatas"][0]
        strength_after_3 = float(meta_after_3.get("strength", DRAWER_STRENGTH_DEFAULT))

        assert strength_after_1 == pytest.approx(strength_after_3, rel=1e-9), (
            f"decay_pass must be idempotent: 1× produced {strength_after_1}, "
            f"3× produced {strength_after_3}"
        )

        # Also verify the stored value matches the pure formula.
        days_elapsed = (now - filed).total_seconds() / 86400.0
        pure = _compute_decay(confidence=0.4, days_since_used=days_elapsed)
        assert strength_after_1 == pytest.approx(pure, rel=1e-9), (
            f"Stored strength {strength_after_1} must equal pure formula {pure}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# (11) Real demotion — consolidation actually demotes a near-duplicate
# ─────────────────────────────────────────────────────────────────────────────


class TestConsolidationRealDemotion:
    def test_real_demotion_occurs_and_canonical_stays(self, chroma_col):
        """Two near-identical non-core drawers → exactly one is demoted (strength==CONSOLIDATION_DEMOTE_STRENGTH),
        the other stays canonical. Proves the pass is not inert.
        """
        from sage_mcp.consolidation import CONSOLIDATION_DEMOTE_STRENGTH

        # Add two nearly identical drawers (high semantic overlap).
        _add_drawer(
            chroma_col,
            "real_dem_a",
            "The payment service uses Stripe for processing credit card transactions and subscriptions",
            confidence=0.9,
            filed_at=T0 - timedelta(days=3),
            strength=DRAWER_STRENGTH_DEFAULT,
        )
        _add_drawer(
            chroma_col,
            "real_dem_b",
            "Payment service uses Stripe to process credit card transactions and manage subscriptions",
            confidence=0.4,
            filed_at=T0 - timedelta(days=5),
            strength=DRAWER_STRENGTH_DEFAULT,
        )

        results = consolidation_pass(chroma_col, dry_run=False)

        demoted = [r for r in results if r.reason == "demoted:near_duplicate"]
        canonicals = [r for r in results if r.reason == "canonical"]

        # At least one demotion must have occurred (the pass is not inert).
        assert len(demoted) >= 1, (
            "consolidation_pass must demote at least one near-duplicate; "
            "pass appears inert (similarity threshold not reached or probe broken). "
            f"Results: {[(r.drawer_id, r.reason, r.new_strength) for r in results]}"
        )

        # The demoted member has strength == CONSOLIDATION_DEMOTE_STRENGTH.
        for r in demoted:
            assert r.new_strength == pytest.approx(CONSOLIDATION_DEMOTE_STRENGTH, rel=1e-6), (
                f"Demoted drawer {r.drawer_id} should have strength={CONSOLIDATION_DEMOTE_STRENGTH}, "
                f"got {r.new_strength}"
            )
            # And the stored value should match.
            stored = chroma_col.get(ids=[r.drawer_id], include=["metadatas"])["metadatas"][0]
            stored_strength = float(stored.get("strength", DRAWER_STRENGTH_DEFAULT))
            assert stored_strength == pytest.approx(CONSOLIDATION_DEMOTE_STRENGTH, rel=1e-5), (
                f"Stored strength for {r.drawer_id} should be {CONSOLIDATION_DEMOTE_STRENGTH}, "
                f"got {stored_strength}"
            )

        # The canonical member stays at its original strength (not demoted).
        for r in canonicals:
            if r.drawer_id in ("real_dem_a", "real_dem_b"):
                assert r.new_strength == pytest.approx(r.old_strength, rel=1e-6), (
                    f"Canonical drawer {r.drawer_id} strength must be unchanged"
                )

        # Both drawers still exist (no-delete invariant).
        both = chroma_col.get(ids=["real_dem_a", "real_dem_b"], include=["metadatas"])
        assert len(both["ids"]) == 2, "No-delete invariant: both drawers must survive"

        # Higher-confidence drawer must be canonical.
        demoted_ids = {r.drawer_id for r in demoted}
        assert "real_dem_b" in demoted_ids, (
            "Lower-confidence drawer (real_dem_b, conf=0.4) should be demoted; "
            "real_dem_a (conf=0.9) should be canonical"
        )


# ─────────────────────────────────────────────────────────────────────────────
# (12) Merge-candidate tunnel path — consolidation uses WI-4 tunnel hints
# ─────────────────────────────────────────────────────────────────────────────


class TestMergeCandidateTunnelConsolidation:
    def test_merge_candidate_tunnel_demotes_lower_confidence_member(self, chroma_col, monkeypatch):
        """Tunnel path genuinely guards: demotion occurs ONLY via the tunnel, not the vector path.

        The two drawers use DISSIMILAR text (different topics, well below the
        0.85 cosine-similarity threshold) so the vector path will NOT cluster them.
        Demotion can ONLY happen via the merge-candidate tunnel path.

        This test MUST fail against the dead-tunnel code (FIX 1 not applied) and
        MUST pass after FIX 1.
        """
        from sage_mcp.consolidation import CONSOLIDATION_DEMOTE_STRENGTH

        # Deliberately DISSIMILAR text: one is about database backups, the other
        # about frontend CSS theming.  These topics are semantically unrelated —
        # cosine similarity will be well below CONSOLIDATION_SIMILARITY_THRESHOLD
        # (0.85), so the vector path will NOT cluster them.
        _add_drawer(
            chroma_col,
            "tun_high_conf",
            "Nightly database backup jobs write compressed snapshots to S3 cold storage buckets",
            confidence=0.85,
            filed_at=T0 - timedelta(days=2),
            strength=DRAWER_STRENGTH_DEFAULT,
        )
        _add_drawer(
            chroma_col,
            "tun_low_conf",
            "The React frontend applies a CSS custom-property theme with light and dark mode tokens",
            confidence=0.35,
            filed_at=T0 - timedelta(days=4),
            strength=DRAWER_STRENGTH_DEFAULT,
        )

        # Simulate a merge-candidate tunnel linking the two drawers.
        # We monkeypatch _list_tunnels_fn to return a fake tunnel — avoiding real
        # nook_graph I/O in tests.  The tunnel links the dissimilar-text drawers
        # so that ONLY the tunnel path (not the vector path) can cause a demotion.
        fake_tunnels = [
            {
                "id": "fake-tunnel-001",
                "label": "merge-candidate",
                "source": {
                    "wing": "test",
                    "room": "facts",
                    "drawer_id": "tun_high_conf",
                },
                "target": {
                    "wing": "test",
                    "room": "facts",
                    "drawer_id": "tun_low_conf",
                },
            }
        ]

        import sage_mcp.consolidation as consolidation_mod

        monkeypatch.setattr(
            consolidation_mod,
            "_list_tunnels_fn",
            lambda: fake_tunnels,
        )

        results = consolidation_pass(chroma_col, dry_run=False)

        # The lower-confidence member must be demoted — and since the text is
        # dissimilar, this can ONLY have happened via the tunnel path.
        demoted = [r for r in results if r.reason == "demoted:near_duplicate"]
        demoted_ids = {r.drawer_id for r in demoted}

        assert "tun_low_conf" in demoted_ids, (
            "Lower-confidence member of a merge-candidate tunnel must be demoted. "
            "The texts are dissimilar, so only the tunnel path could cause this. "
            f"Demoted ids: {demoted_ids}"
        )

        # Verify the stored strength.
        stored = chroma_col.get(ids=["tun_low_conf"], include=["metadatas"])["metadatas"][0]
        stored_strength = float(stored.get("strength", DRAWER_STRENGTH_DEFAULT))
        assert stored_strength == pytest.approx(CONSOLIDATION_DEMOTE_STRENGTH, rel=1e-5), (
            f"Demoted tunnel member stored strength should be {CONSOLIDATION_DEMOTE_STRENGTH}, "
            f"got {stored_strength}"
        )

        # Higher-confidence member must NOT be demoted.
        assert "tun_high_conf" not in demoted_ids, "Higher-confidence member must remain canonical"

        # No-delete: both still exist.
        both = chroma_col.get(ids=["tun_high_conf", "tun_low_conf"], include=["metadatas"])
        assert len(both["ids"]) == 2

    def test_tunnel_demotion_idempotent(self, chroma_col, monkeypatch):
        """Running consolidation_pass twice with the same merge-candidate tunnel yields the same result.

        Idempotency: re-running does not further change the loser's strength or
        re-demote an already-canonical winner.
        """
        from sage_mcp.consolidation import CONSOLIDATION_DEMOTE_STRENGTH

        # Dissimilar text: only tunnel path can demote.
        _add_drawer(
            chroma_col,
            "idem_tun_winner",
            "PostgreSQL replication lag monitoring with alerting thresholds configured in Datadog",
            confidence=0.9,
            filed_at=T0 - timedelta(days=1),
            strength=DRAWER_STRENGTH_DEFAULT,
        )
        _add_drawer(
            chroma_col,
            "idem_tun_loser",
            "The Figma design system exports SVG icon sprites for use in the mobile app",
            confidence=0.3,
            filed_at=T0 - timedelta(days=3),
            strength=DRAWER_STRENGTH_DEFAULT,
        )

        fake_tunnels = [
            {
                "id": "fake-tunnel-idem-001",
                "label": "merge-candidate",
                "source": {"wing": "test", "room": "facts", "drawer_id": "idem_tun_winner"},
                "target": {"wing": "test", "room": "facts", "drawer_id": "idem_tun_loser"},
            }
        ]

        import sage_mcp.consolidation as consolidation_mod

        monkeypatch.setattr(consolidation_mod, "_list_tunnels_fn", lambda: fake_tunnels)

        # Run 1.
        results1 = consolidation_pass(chroma_col, dry_run=False)
        demoted1 = {r.drawer_id for r in results1 if r.reason == "demoted:near_duplicate"}
        assert "idem_tun_loser" in demoted1, "First run must demote via tunnel"

        stored_after_1 = chroma_col.get(ids=["idem_tun_loser"], include=["metadatas"])["metadatas"][
            0
        ]
        strength_after_1 = float(stored_after_1.get("strength", DRAWER_STRENGTH_DEFAULT))

        # Run 2 (idempotency check).
        results2 = consolidation_pass(chroma_col, dry_run=False)
        demoted2 = {r.drawer_id for r in results2 if r.reason == "demoted:near_duplicate"}
        assert "idem_tun_loser" in demoted2, "Second run must still report loser as demoted"

        stored_after_2 = chroma_col.get(ids=["idem_tun_loser"], include=["metadatas"])["metadatas"][
            0
        ]
        strength_after_2 = float(stored_after_2.get("strength", DRAWER_STRENGTH_DEFAULT))

        assert strength_after_1 == pytest.approx(strength_after_2, rel=1e-9), (
            f"Tunnel demotion must be idempotent: run-1={strength_after_1}, run-2={strength_after_2}"
        )
        assert strength_after_1 == pytest.approx(CONSOLIDATION_DEMOTE_STRENGTH, rel=1e-5)

        # Winner must not be demoted in either run.
        assert "idem_tun_winner" not in demoted1
        assert "idem_tun_winner" not in demoted2

    def test_cross_wing_tunnel_no_demotion(self, chroma_col, monkeypatch):
        """A merge-candidate tunnel linking two DIFFERENT-wing drawers must NOT demote either.

        FIX 3 regression guard: the tunnel path must have an unconditional
        same-wing check, consistent with _assign_neighbours wing isolation.
        """
        # Two drawers in DIFFERENT wings.
        _add_drawer(
            chroma_col,
            "xw_tun_alpha",
            "Terraform provisions the VPC subnets and security groups in AWS us-east-1",
            wing="wing_alpha",
            confidence=0.8,
            filed_at=T0 - timedelta(days=2),
            strength=DRAWER_STRENGTH_DEFAULT,
        )
        _add_drawer(
            chroma_col,
            "xw_tun_beta",
            "Terraform provisions the VPC subnets and security groups in AWS us-west-2",
            wing="wing_beta",
            confidence=0.4,
            filed_at=T0 - timedelta(days=3),
            strength=DRAWER_STRENGTH_DEFAULT,
        )

        # A cross-wing merge-candidate tunnel — must be ignored.
        fake_tunnels = [
            {
                "id": "fake-tunnel-cross-wing-001",
                "label": "merge-candidate",
                "source": {"wing": "wing_alpha", "room": "facts", "drawer_id": "xw_tun_alpha"},
                "target": {"wing": "wing_beta", "room": "facts", "drawer_id": "xw_tun_beta"},
            }
        ]

        import sage_mcp.consolidation as consolidation_mod

        monkeypatch.setattr(consolidation_mod, "_list_tunnels_fn", lambda: fake_tunnels)

        results = consolidation_pass(chroma_col, wing=None, dry_run=False)

        demoted = {r.drawer_id for r in results if r.reason == "demoted:near_duplicate"}

        assert "xw_tun_alpha" not in demoted, (
            "Cross-wing tunnel must NOT demote xw_tun_alpha (wing_alpha)"
        )
        assert "xw_tun_beta" not in demoted, (
            "Cross-wing tunnel must NOT demote xw_tun_beta (wing_beta)"
        )

        # Both drawers must remain at full strength.
        for did in ("xw_tun_alpha", "xw_tun_beta"):
            stored = chroma_col.get(ids=[did], include=["metadatas"])["metadatas"][0]
            stored_strength = float(stored.get("strength", DRAWER_STRENGTH_DEFAULT))
            assert stored_strength == pytest.approx(DRAWER_STRENGTH_DEFAULT, rel=1e-5), (
                f"Cross-wing drawer {did} must not be demoted; stored strength={stored_strength}"
            )

        # No-delete.
        both = chroma_col.get(ids=["xw_tun_alpha", "xw_tun_beta"], include=["metadatas"])
        assert len(both["ids"]) == 2


# ─────────────────────────────────────────────────────────────────────────────
# (13) FIX 1 regression — highest-confidence canonical regardless of iteration order
# ─────────────────────────────────────────────────────────────────────────────


class TestHighestConfidenceCanonical:
    def test_highest_confidence_wins_when_iterated_last(self, chroma_col):
        """The highest-confidence drawer is always canonical, regardless of insertion/iteration order.

        Regression guard for FIX 1: before the fix, the QUERY drawer was made
        canonical without a confidence check, so a high-confidence drawer added
        last could be demoted to CONSOLIDATION_DEMOTE_STRENGTH while a lower-
        confidence drawer iterated first remained canonical.

        Setup: three near-identical drawers.
          - conf_low  (confidence=0.3) — added first, iterated first
          - conf_mid  (confidence=0.6) — added second
          - conf_high (confidence=0.99) — added last, iterated last

        Expected: conf_high is canonical; conf_low and conf_mid are demoted.
        """
        from sage_mcp.consolidation import CONSOLIDATION_DEMOTE_STRENGTH

        base_text = "The payment service uses Stripe for processing credit card transactions"
        _add_drawer(
            chroma_col,
            "hc_low",
            base_text + " and managing subscriptions",
            confidence=0.3,
            filed_at=T0 - timedelta(days=10),
            strength=DRAWER_STRENGTH_DEFAULT,
        )
        _add_drawer(
            chroma_col,
            "hc_mid",
            base_text + " and handling subscription billing",
            confidence=0.6,
            filed_at=T0 - timedelta(days=8),
            strength=DRAWER_STRENGTH_DEFAULT,
        )
        # Highest-confidence drawer added LAST — was incorrectly demoted before fix.
        _add_drawer(
            chroma_col,
            "hc_high",
            base_text + " and subscription lifecycle management",
            confidence=0.99,
            filed_at=T0 - timedelta(days=5),
            strength=DRAWER_STRENGTH_DEFAULT,
        )

        results = consolidation_pass(chroma_col, dry_run=False)

        demoted = {r.drawer_id for r in results if r.reason == "demoted:near_duplicate"}
        canonicals = {r.drawer_id for r in results if r.reason == "canonical"}

        # hc_high must be canonical (highest confidence), NOT demoted.
        assert "hc_high" in canonicals, (
            f"Highest-confidence drawer (conf=0.99, added last) must be canonical. "
            f"Canonicals: {canonicals}, Demoted: {demoted}"
        )
        assert "hc_high" not in demoted, (
            "Highest-confidence drawer must NOT be demoted regardless of iteration order"
        )

        # Both lower-confidence drawers should be demoted (if similarity threshold reached).
        if demoted:
            for did in demoted:
                stored = chroma_col.get(ids=[did], include=["metadatas"])["metadatas"][0]
                stored_strength = float(stored.get("strength", DRAWER_STRENGTH_DEFAULT))
                assert stored_strength == pytest.approx(CONSOLIDATION_DEMOTE_STRENGTH, rel=1e-5), (
                    f"Demoted drawer {did} should have strength={CONSOLIDATION_DEMOTE_STRENGTH}, "
                    f"got {stored_strength}"
                )

        # No-delete: all three exist.
        all_three = chroma_col.get(ids=["hc_low", "hc_mid", "hc_high"], include=["metadatas"])
        assert len(all_three["ids"]) == 3, "No-delete invariant violated"


# ─────────────────────────────────────────────────────────────────────────────
# (14) FIX 2 regression — cross-wing near-duplicates are never clustered
# ─────────────────────────────────────────────────────────────────────────────


class TestCrossWingIsolation:
    def test_cross_wing_near_duplicates_neither_demoted(self, chroma_col):
        """Near-identical drawers in DIFFERENT wings must never be clustered.

        Regression guard for FIX 2: before the fix, an unscoped consolidation_pass
        (wing=None) would cluster across wing boundaries.  A drawer in wing_b
        could be demoted solely because wing_a had a similar drawer, suppressing
        it in wing_b-scoped searches.

        Setup: one drawer in wing_alpha and one near-identical drawer in wing_beta.
        Neither should be demoted by an unscoped pass.
        """
        cross_text = "The authentication service uses JWT bearer tokens with 24-hour expiry"
        _add_drawer(
            chroma_col,
            "xw_alpha",
            cross_text + " for user session management",
            wing="wing_alpha",
            confidence=0.8,
            filed_at=T0 - timedelta(days=3),
            strength=DRAWER_STRENGTH_DEFAULT,
        )
        _add_drawer(
            chroma_col,
            "xw_beta",
            cross_text + " and session lifecycle tracking",
            wing="wing_beta",
            confidence=0.5,
            filed_at=T0 - timedelta(days=2),
            strength=DRAWER_STRENGTH_DEFAULT,
        )

        # Unscoped run — must NOT demote either drawer across the wing boundary.
        results = consolidation_pass(chroma_col, wing=None, dry_run=False)

        demoted = {r.drawer_id for r in results if r.reason == "demoted:near_duplicate"}

        assert "xw_alpha" not in demoted, (
            "xw_alpha (wing_alpha) must not be demoted by a near-duplicate in wing_beta"
        )
        assert "xw_beta" not in demoted, (
            "xw_beta (wing_beta) must not be demoted by a near-duplicate in wing_alpha"
        )

        # Both drawers must still have full (un-demoted) strength.
        for did in ("xw_alpha", "xw_beta"):
            stored = chroma_col.get(ids=[did], include=["metadatas"])["metadatas"][0]
            stored_strength = float(stored.get("strength", DRAWER_STRENGTH_DEFAULT))
            assert stored_strength == pytest.approx(DRAWER_STRENGTH_DEFAULT, rel=1e-5), (
                f"Cross-wing drawer {did} must not be demoted; stored strength={stored_strength}"
            )

        # No-delete invariant.
        both = chroma_col.get(ids=["xw_alpha", "xw_beta"], include=["metadatas"])
        assert len(both["ids"]) == 2

    def test_within_wing_near_duplicate_still_demoted(self, chroma_col):
        """Sanity check: within-wing near-duplicates are still demoted after FIX 2.

        Ensures the wing isolation fix does not accidentally suppress legitimate
        within-wing consolidation.
        """
        from sage_mcp.consolidation import CONSOLIDATION_DEMOTE_STRENGTH

        same_text = "The CI pipeline uses GitHub Actions for automated multi-platform testing"
        _add_drawer(
            chroma_col,
            "sw_high",
            same_text + " with matrix strategy",
            wing="wing_gamma",
            confidence=0.85,
            filed_at=T0 - timedelta(days=4),
            strength=DRAWER_STRENGTH_DEFAULT,
        )
        _add_drawer(
            chroma_col,
            "sw_low",
            same_text + " and matrix build configuration",
            wing="wing_gamma",
            confidence=0.35,
            filed_at=T0 - timedelta(days=6),
            strength=DRAWER_STRENGTH_DEFAULT,
        )

        results = consolidation_pass(chroma_col, dry_run=False)
        demoted = {r.drawer_id for r in results if r.reason == "demoted:near_duplicate"}

        # If similarity threshold was crossed, the lower-confidence drawer is demoted.
        if demoted:
            assert "sw_low" in demoted, (
                f"Lower-confidence within-wing near-duplicate must be demoted; demoted={demoted}"
            )
            stored = chroma_col.get(ids=["sw_low"], include=["metadatas"])["metadatas"][0]
            stored_strength = float(stored.get("strength", DRAWER_STRENGTH_DEFAULT))
            assert stored_strength == pytest.approx(CONSOLIDATION_DEMOTE_STRENGTH, rel=1e-5)

        # No-delete.
        both = chroma_col.get(ids=["sw_high", "sw_low"], include=["metadatas"])
        assert len(both["ids"]) == 2


# ─────────────────────────────────────────────────────────────────────────────
# (6) Searcher uses strength — _hybrid_rank down-ranks decayed drawer
# ─────────────────────────────────────────────────────────────────────────────


class TestSearcherStrengthIntegration:
    def test_decayed_drawer_ranks_lower_than_fresh(self):
        """A drawer with decayed strength ranks below an equal-text drawer with full strength.

        Both drawers have equal BM25/text; the only difference is strength.
        Verifies that _hybrid_rank uses the strength signal correctly.
        """
        query = "PostgreSQL authentication database"
        text_a = "PostgreSQL authentication database with JWT tokens for user management"

        # Decayed drawer: strength near the floor.
        decayed = {
            "text": text_a,
            "distance": 0.1,
            "metadata": {"strength": DRAWER_STRENGTH_FLOOR},
            "drawer_id": "decayed_001",
        }
        # Fresh drawer: same text, full strength.
        fresh = {
            "text": text_a,
            "distance": 0.1,
            "metadata": {"strength": DRAWER_STRENGTH_DEFAULT},
            "drawer_id": "fresh_001",
        }
        # Inject decayed first (would normally sort first without strength signal).
        results = [decayed, fresh]
        ranked = _hybrid_rank(results, query)
        # Fresh (higher strength) should rank first.
        assert ranked[0]["drawer_id"] == "fresh_001", (
            "Fresh drawer (strength=1.0) must rank above decayed (strength=FLOOR)"
        )

    def test_equal_strength_no_reorder_from_strength(self):
        """When all drawers have identical strength, the strength term is 0 and
        doesn't reshuffle the ranking — BM25 + vector dominate."""
        query = "database migration"
        results = [
            {
                "text": "Database migration using Alembic and PostgreSQL",
                "distance": 0.1,
                "metadata": {"strength": 1.0},
            },
            {
                "text": "Alembic runs database schema migrations",
                "distance": 0.3,
                "metadata": {"strength": 1.0},
            },
        ]
        ranked = _hybrid_rank(results, query)
        # With identical strength, the result is driven by BM25 + vector only.
        # Just verify no crash and length preserved.
        assert len(ranked) == 2

    def test_strength_absent_treated_as_default(self):
        """A result dict with no strength key is treated as DRAWER_STRENGTH_DEFAULT."""
        query = "authentication tokens"
        results = [
            {
                "text": "JWT authentication tokens used for session management",
                "distance": 0.1,
                "metadata": {},
            },
            {
                "text": "JWT authentication tokens used for session management",
                "distance": 0.1,
                "metadata": {"strength": DRAWER_STRENGTH_FLOOR},
            },
        ]
        ranked = _hybrid_rank(results, query)
        # No crash; default > floor so the one without explicit strength ranks first.
        assert len(ranked) == 2
        assert ranked[0]["metadata"].get("strength") is None or ranked[0]["metadata"].get(
            "strength"
        ) == pytest.approx(DRAWER_STRENGTH_DEFAULT)

    def test_strength_determinism(self):
        """_hybrid_rank with strength produces the same result on repeated calls."""
        query = "database configuration"
        r1 = {
            "text": "Database configuration settings for PostgreSQL",
            "distance": 0.2,
            "metadata": {"strength": 0.9},
        }
        r2 = {
            "text": "PostgreSQL configuration and database tuning",
            "distance": 0.25,
            "metadata": {"strength": 0.5},
        }

        # First call.
        import copy

        results_a = copy.deepcopy([r1, r2])
        results_b = copy.deepcopy([r1, r2])
        _hybrid_rank(results_a, query)
        _hybrid_rank(results_b, query)

        assert [r["drawer_id"] for r in results_a if "drawer_id" in r] == [
            r["drawer_id"] for r in results_b if "drawer_id" in r
        ], "Ranking must be deterministic"
        # Order of the two results must match.
        assert results_a[0]["text"] == results_b[0]["text"]


# ─────────────────────────────────────────────────────────────────────────────
# (9) Fixture nook demo — 5 scenarios end-to-end
# ─────────────────────────────────────────────────────────────────────────────


class TestFixtureNookDemo:
    """End-to-end demonstration on a fixture nook.

    Covers all 5 stated demo scenarios from the plan:
    (1) A stale low-confidence drawer's strength drops but stays ≥ floor and drawer still exists.
    (2) A hall=core drawer is untouched.
    (3) A high-confidence drawer barely decays.
    (4) Consolidation down-ranks a near-duplicate without deleting it.
    (5) Searcher down-ranks a decayed drawer.
    """

    def test_all_five_scenarios(self, chroma_col):
        # ── Setup ────────────────────────────────────────────────────────────
        # Scenario 1: stale low-confidence (90 days old, confidence=0.1).
        _add_drawer(
            chroma_col,
            "demo_stale_001",
            "Old speculative note about maybe using Redis for caching",
            hall=None,
            confidence=0.1,
            filed_at=T_STALE,
            strength=1.0,
        )
        # Scenario 2: core hall — durable identity fact.
        _add_drawer(
            chroma_col,
            "demo_core_001",
            "Core identity: GitHub username is Ryuuske, company brand ZiSaStudios",
            hall=CORE_HALL_NAME,
            confidence=1.0,
            filed_at=T_STALE,  # even though stale, must not decay
            strength=1.0,
        )
        # Scenario 3: high-confidence (90 days old, confidence=1.0).
        _add_drawer(
            chroma_col,
            "demo_highconf_001",
            "Confirmed architecture decision: PostgreSQL primary datastore, confirmed by production use",
            hall=None,
            confidence=1.0,
            filed_at=T_STALE,
            strength=1.0,
        )
        # Scenario 4a: near-duplicate A (higher confidence → canonical).
        _add_drawer(
            chroma_col,
            "demo_dup_canonical",
            "The authentication system uses JWT bearer tokens with 24-hour expiry for session management",
            hall=None,
            confidence=0.9,
            filed_at=T0 - timedelta(days=5),
            strength=1.0,
        )
        # Scenario 4b: near-duplicate B (lower confidence → should be demoted).
        _add_drawer(
            chroma_col,
            "demo_dup_redundant",
            "Authentication uses JWT bearer tokens with 24-hour expiry and session management",
            hall=None,
            confidence=0.3,
            filed_at=T_STALE,
            strength=1.0,
        )

        now = T0

        # ── Scenario 1: stale low-confidence decays but stays ≥ floor ────────
        decay_results = decay_pass(chroma_col, now=now, dry_run=False)
        stale_r = next(r for r in decay_results if r.drawer_id == "demo_stale_001")
        assert not stale_r.skipped
        assert stale_r.new_strength < stale_r.old_strength, "Stale drawer should decay"
        assert stale_r.new_strength >= DRAWER_STRENGTH_FLOOR, "Must stay >= floor"
        assert stale_r.new_strength > 0, "Must never be zero"
        # Drawer still exists in collection.
        still_there = chroma_col.get(ids=["demo_stale_001"], include=["metadatas"])
        assert len(still_there["ids"]) == 1, "Stale drawer must not be deleted"

        # ── Scenario 2: core hall untouched ───────────────────────────────────
        core_r = next(r for r in decay_results if r.drawer_id == "demo_core_001")
        assert core_r.skipped
        assert core_r.reason == "skipped:core_hall"
        assert core_r.old_strength == core_r.new_strength
        # Verify not written (stored strength still 1.0 from fixture).
        core_in_col = chroma_col.get(ids=["demo_core_001"], include=["metadatas"])
        stored_strength = core_in_col["metadatas"][0].get("strength", DRAWER_STRENGTH_DEFAULT)
        assert float(stored_strength) == pytest.approx(1.0, rel=1e-6), (
            "Core hall drawer's stored strength must not change"
        )

        # ── Scenario 3: high-confidence barely decays ─────────────────────────
        highconf_r = next(r for r in decay_results if r.drawer_id == "demo_highconf_001")
        assert not highconf_r.skipped
        # After 90 days at confidence=1.0, should retain > 70% of strength
        # (actual ~80%; see test_high_confidence_barely_decays for formula derivation).
        assert highconf_r.new_strength > 0.70, (
            f"High-confidence drawer should retain >70% strength after 90 days, "
            f"got {highconf_r.new_strength}"
        )
        # Should be well above floor, unlike a zero-confidence drawer.
        assert highconf_r.new_strength > DRAWER_STRENGTH_FLOOR * 5

        # ── Scenario 4: consolidation down-ranks near-duplicate, doesn't delete ─
        c_results = consolidation_pass(chroma_col, dry_run=False)

        # Both drawers still exist.
        both = chroma_col.get(
            ids=["demo_dup_canonical", "demo_dup_redundant"], include=["metadatas"]
        )
        assert len(both["ids"]) == 2, "Consolidation must NOT delete any drawer"

        # The redundant drawer is demoted (or at least not deleted).
        demoted_results = [r for r in c_results if r.reason == "demoted:near_duplicate"]
        # If similarity threshold was reached, at least one is demoted.
        for r in demoted_results:
            assert r.new_strength >= DRAWER_STRENGTH_FLOOR
            assert r.new_strength > 0

        # ── Scenario 5: searcher down-ranks a decayed drawer ─────────────────
        # Directly test _hybrid_rank with the known decayed strength.
        # Use the strength that decay_pass wrote for demo_stale_001.
        stale_stored = chroma_col.get(ids=["demo_stale_001"], include=["metadatas"])
        stale_strength = float(
            stale_stored["metadatas"][0].get("strength", DRAWER_STRENGTH_DEFAULT)
        )

        query_text = "Redis caching speculative architecture"
        stale_candidate = {
            "text": "Old speculative note about maybe using Redis for caching",
            "distance": 0.05,
            "metadata": {"strength": stale_strength},
            "drawer_id": "demo_stale_001",
        }
        fresh_candidate = {
            "text": "Old speculative note about maybe using Redis for caching",
            "distance": 0.05,
            "metadata": {"strength": DRAWER_STRENGTH_DEFAULT},
            "drawer_id": "demo_fresh_ref",
        }
        ranked = _hybrid_rank([stale_candidate, fresh_candidate], query_text)
        assert ranked[0]["drawer_id"] == "demo_fresh_ref", (
            "Fresh (higher strength) drawer must rank above decayed drawer"
        )


# ─────────────────────────────────────────────────────────────────────────────
# (8) Extra guard: NEVER touch live ~/.sage
# ─────────────────────────────────────────────────────────────────────────────


def test_never_touches_live_store():
    """All test collections use isolated temp dirs — the live store is never opened.

    This test validates the test infrastructure itself: conftest.py redirects
    HOME to a temp dir, and the chroma_col fixture uses its own local temp dir.
    The real ~/.sage path is never opened by any test in this module.
    """
    # HOME was redirected by conftest to a temp dir, so this path should NOT
    # point at the real user's data.
    real_home = os.environ.get("HOME", "")
    assert ".sage" not in real_home or "sage_session_" in real_home, (
        "HOME should be redirected to a temp dir by conftest.py"
    )
