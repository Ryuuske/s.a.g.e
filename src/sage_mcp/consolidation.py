"""consolidation.py — Drawer-level decay and consolidation passes.

WI-6: Down-rank stale or redundant drawers so retrieval quality does not rot
as the nook grows, WITHOUT ever deleting a drawer.

## Design invariants

1. **No-delete floor** (structural invariant): ``decay_pass`` and
   ``consolidation_pass`` may only write to the ``strength`` metadata field.
   They NEVER call ``col.delete`` on drawers. Every drawer remains queryable
   at its full text; only its retrieval rank changes. The floor
   ``DRAWER_STRENGTH_FLOOR`` (default 0.1) guarantees a minimum salience;
   even the most stale drawer is recoverable by an explicit ``nook_search``
   or by re-ranking from provenance.

2. **``hall=core`` exclusion**: Durable identity facts stored in the
   Personal wing's ``core`` hall (WI-5 Tier-0 source) are NEVER decayed or
   consolidated. Their strength is untouched and always reads as
   ``DRAWER_STRENGTH_DEFAULT`` unless the caller explicitly passed a prior
   custom strength.

3. **Confidence-weighted decay rate (PURE / IDEMPOTENT)**: Decay scales inversely
   with the drawer's ``confidence`` metadata tag (WI-5). A confidence=1.0 drawer
   decays very slowly (rate multiplied by ``1 - HIGH_CONFIDENCE_PROTECTION``); a
   confidence=0.0 drawer decays at the full base rate. This is separate from
   the KG-connection dynamics in ``dynamics.py`` — we do NOT use that module
   here.

   The decay formula is pure and idempotent — it NEVER reads the prior stored
   ``strength`` value. Strength is always recomputed from ``DRAWER_STRENGTH_DEFAULT``
   so multiple decay runs on the same drawer with the same reference time
   produce the same result. A bad decay run is reversible by recomputing:

       effective_rate = BASE_DECAY_RATE * (1 - confidence * HIGH_CONFIDENCE_PROTECTION)
       strength = DRAWER_STRENGTH_DEFAULT * exp(-age_days * effective_rate)
       final_strength = max(DRAWER_STRENGTH_FLOOR, strength)

   where ``age_days = (now - (last_used or filed_at or created))``.
   ``last_used`` is read when present but is NOT written by any current code
   path — decay effectively uses ``filed_at`` age in practice.
   ``last_used``-based recency-decay (resetting effective age on retrieval)
   is a future enhancement (see ADR-0043 Consequences). The prior stored
   ``strength`` is read for reporting only, never fed back into the formula.

4. **Single-writer**: Strength writes go through ``col.update`` (Chroma
   metadata update). Runtime callers should always mediate through the nook
   write path (Keeper) per CLAUDE.md §9; direct use is only for the CLI
   and tests.

5. **Safe-mode default**: The CLI ``consolidate`` command defaults to
   ``--dry-run`` (report-only). Strength changes only apply with ``--apply``.

## Relationship to dynamics.py

``dynamics.py`` implements Hebbian potentiation + Ebbinghaus decay for
KG *connections* (halls/tunnels) — not for drawers. The ``STRENGTH_FLOOR``
in ``dynamics.py`` (0.05) is the KG-connection floor. This module has its
own drawer-level floor (``DRAWER_STRENGTH_FLOOR = 0.1``) for a distinct
concept. We borrow the exponential-decay formula shape but compute it
independently for drawers.

## Usage (programmatic)

    from sage_mcp.consolidation import decay_pass, consolidation_pass

    # Dry-run: see what would change, nothing written.
    results = decay_pass(col, now=datetime.now(timezone.utc), dry_run=True)
    for r in results:
        print(r.drawer_id, r.old_strength, "->", r.new_strength, r.reason)

    # Apply: write updated strengths to metadata.
    results = decay_pass(col, now=datetime.now(timezone.utc), dry_run=False)

    # Consolidation: down-rank near-duplicate drawers.
    c_results = consolidation_pass(col, dry_run=True)

## Usage (CLI)

    sage consolidate report             # dry-run: show what would change
    sage consolidate run                # apply decay + consolidation
    sage consolidate run --wing my_app  # scope to one wing
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def _list_tunnels_fn():
    """Lazy wrapper around nook_graph.list_tunnels.

    This module-level reference exists so tests can monkeypatch it
    (``monkeypatch.setattr(consolidation, "_list_tunnels_fn", fake_fn)``)
    without needing real nook_graph I/O. The lazy import also avoids a
    circular import at module load time.
    """
    from .nook_graph import list_tunnels  # noqa: PLC0415

    return list_tunnels()


# ─────────────────────────────────────────────────────────────────────────────
# Tunable constants — drawer-level decay
# ─────────────────────────────────────────────────────────────────────────────

DRAWER_STRENGTH_DEFAULT = 1.0
"""Default strength for all drawers that do not yet carry a strength tag.
Stored on write (in ``tool_add_drawer``'s base_meta) from WI-5 onward;
older drawers that never got the field are treated as DEFAULT at read-time."""

DRAWER_STRENGTH_FLOOR = 0.1
"""Lower bound on drawer strength. No drawer can drop below this, ever.
The nook never forgets — salience just drops. This floor guarantees every
drawer remains reachable by an explicit search regardless of staleness.
Separate from dynamics.py's STRENGTH_FLOOR (0.05), which is KG-connection-
scoped."""

BASE_DECAY_RATE = 0.05
"""Per-day Ebbinghaus decay rate for a fully-unconfident (confidence=0)
drawer. After 1/BASE_DECAY_RATE days (~20 days) at confidence=0, strength
will have decayed by exp(-1) ≈ 0.37× from its current value, bounded by the
floor. High-confidence drawers decay much more slowly via
HIGH_CONFIDENCE_PROTECTION."""

HIGH_CONFIDENCE_PROTECTION = 0.95
"""Fraction of the decay rate that high-confidence (confidence=1.0) drawers
are shielded from. A confidence=1.0 drawer's effective_rate is:
    BASE_DECAY_RATE * (1 - 1.0 * 0.95) = BASE_DECAY_RATE * 0.05 = 0.0025/day
which means it decays ~20× slower than a confidence=0 drawer. After 90 days,
a confidence=1.0 drawer retains ~exp(-90*0.0025) ≈ 80% of its strength, while
a confidence=0.0 drawer would decay to the floor. A drawer at confidence=0.5
decays at BASE_DECAY_RATE * (1 - 0.5*0.95) = BASE_DECAY_RATE * 0.525,
roughly halving the decay speed compared to zero-confidence."""

CONSOLIDATION_SIMILARITY_THRESHOLD = 0.85
"""Cosine similarity at which two drawers are considered near-duplicates for
consolidation purposes. Drawers above this threshold that are NOT the
canonical representative are down-ranked to ``CONSOLIDATION_DEMOTE_STRENGTH``.
This mirrors the WI-4 MERGE-CANDIDATE band (0.85–0.90)."""

CONSOLIDATION_DEMOTE_STRENGTH = 0.2
"""Strength written to a demoted near-duplicate in consolidation_pass.
Above DRAWER_STRENGTH_FLOOR (never 0, never deleted), but low enough to
suppress it in typical retrieval while keeping it queryable."""

CORE_HALL_NAME = "core"
"""RESERVED PROTECTED hall name — shared single source of truth (ADR-0043).

``core`` is a RESERVED protected hall name across ALL wings.  Any drawer in a
``core`` hall is treated as durable identity and is EXCLUDED from both decay
and consolidation passes entirely (WI-5 / WI-6 invariant).

``layers.py`` (Tier-0 sourcing) imports this constant as ``_CORE_HALL_NAME``
so that a future rename updates both the decay-exclusion guard in this module
AND the Tier-0 identity-core query in layers.py atomically.  Do NOT add a
second ``"core"`` literal in other modules — import this constant instead.

See ADR-0043 for the architectural rationale (reserved-hall semantic,
wing-agnostic match is intentional, not latent)."""

# ─────────────────────────────────────────────────────────────────────────────
# Data classes for pass results
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class DrawerDecayResult:
    """Record of one drawer's decay outcome from ``decay_pass``.

    Both ``old_strength`` and ``new_strength`` are present regardless of
    ``dry_run``; the only difference is whether the write was committed.
    """

    drawer_id: str
    wing: str
    room: str
    hall: Optional[str]
    old_strength: float
    new_strength: float
    confidence: float
    days_since_used: float
    reason: str
    skipped: bool = False  # True when hall=core or otherwise excluded
    written: bool = False  # True when the update was committed to the store


@dataclass
class DrawerConsolidationResult:
    """Record of one drawer's consolidation outcome from ``consolidation_pass``."""

    drawer_id: str
    wing: str
    room: str
    hall: Optional[str]
    old_strength: float
    new_strength: float
    canonical_id: Optional[str]  # the kept representative; None if this IS canonical
    reason: str
    skipped: bool = False
    written: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _parse_iso(value) -> Optional[datetime]:
    """Parse an ISO-8601 string into a timezone-aware datetime.

    Returns None on any parse failure — same safe-fallback contract as
    dynamics.py's _parse_iso. Mirrors that function but lives here to keep
    consolidation.py independently importable.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        v = value.strip()
        if v.endswith("Z"):
            v = v[:-1] + "+00:00"
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _days_since(timestamp_str: Optional[str], now: datetime) -> Optional[float]:
    """Return days elapsed since ``timestamp_str`` relative to ``now``.

    Returns None when the timestamp cannot be parsed — callers treat this as
    "staleness unknown" and skip decay for the drawer rather than corrupting
    its strength.
    """
    dt = _parse_iso(timestamp_str)
    if dt is None:
        return None
    delta = (now - dt).total_seconds()
    return max(0.0, delta / 86400.0)


def _current_strength(meta: dict) -> float:
    """Read a drawer's current strength from metadata, defaulting to DRAWER_STRENGTH_DEFAULT."""
    raw = meta.get("strength")
    if raw is None:
        return DRAWER_STRENGTH_DEFAULT
    try:
        return float(raw)
    except (TypeError, ValueError):
        return DRAWER_STRENGTH_DEFAULT


def _current_confidence(meta: dict) -> float:
    """Read a drawer's confidence from metadata, clamping to [0.0, 1.0]."""
    raw = meta.get("confidence")
    if raw is None:
        return 1.0  # Default: fully confident (mirrors WI-5 default)
    try:
        val = float(raw)
        return max(0.0, min(1.0, val))
    except (TypeError, ValueError):
        return 1.0


def _is_core_hall(meta: dict) -> bool:
    """Return True if this drawer lives in a hall=core (durable identity fact)."""
    return meta.get("hall") == CORE_HALL_NAME


def _compute_decay(
    confidence: float,
    days_since_used: float,
) -> float:
    """Compute strength purely from provenance (age + confidence).

    This is the IDEMPOTENT decay formula — it NEVER reads the prior stored
    ``strength`` value. Strength is always computed from first principles so
    running decay N times yields the same result as running it once, and a
    bad run is undone by simply recomputing.

    Formula:
        effective_rate = BASE_DECAY_RATE * (1 - confidence * HIGH_CONFIDENCE_PROTECTION)
        strength = DRAWER_STRENGTH_DEFAULT * exp(-days_since_used * effective_rate)
        final = max(DRAWER_STRENGTH_FLOOR, strength)

    The anchor is always ``DRAWER_STRENGTH_DEFAULT`` (1.0), not whatever
    strength is stored. Running decay multiple times on the same drawer with
    the same ``now`` timestamp therefore produces identical output — the
    function is path-independent.

    Deterministic: same inputs always produce the same output.
    Pure: no I/O, no mutation, no reads from the store.
    """
    protection = confidence * HIGH_CONFIDENCE_PROTECTION
    effective_rate = BASE_DECAY_RATE * (1.0 - protection)
    decay_factor = math.exp(-days_since_used * effective_rate)
    new_strength = DRAWER_STRENGTH_DEFAULT * decay_factor
    return max(DRAWER_STRENGTH_FLOOR, new_strength)


# ─────────────────────────────────────────────────────────────────────────────
# Decay pass
# ─────────────────────────────────────────────────────────────────────────────


def decay_pass(
    col,
    *,
    now: Optional[datetime] = None,
    wing: Optional[str] = None,
    dry_run: bool = True,
    page_size: int = 500,
) -> list[DrawerDecayResult]:
    """Recompute each non-core drawer's ``strength`` from age + confidence.

    This is the DRAWER-scoped decay pass introduced by WI-6. It is entirely
    separate from the KG-connection decay in ``dynamics.py``.

    Args:
        col:       A ChromaDB collection object (``nook_drawers`` collection).
        now:       Reference time for age calculation. Defaults to UTC now.
                   Pass explicitly in tests for determinism.
        wing:      Optional wing filter. When set, only drawers in this wing
                   are processed.
        dry_run:   When True (default), compute new strengths but do NOT write
                   them. When False, commit each updated strength via
                   ``col.update`` on drawers whose strength actually changes.
        page_size: Batch size for paginated collection scan.

    Returns:
        List of ``DrawerDecayResult`` records — one per drawer examined.
        ``skipped=True`` entries were excluded (hall=core or parse failures).
        ``written=True`` entries had their strength updated in the store.

    Structural invariant: No drawer is ever deleted. This is enforced by
    design — this function only calls ``col.update`` with a new ``strength``
    value; it never calls ``col.delete``. The floor ensures ``new_strength``
    is always ≥ ``DRAWER_STRENGTH_FLOOR`` > 0.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        # FIX 4 (WI6-5): coerce naive ``now`` to UTC so age comparisons are
        # consistent with _parse_iso, which also treats naive filed_at strings
        # as UTC (see _parse_iso: ``dt.replace(tzinfo=timezone.utc)``).
        # Without this, a naive ``now`` would raise TypeError on subtraction
        # against a timezone-aware filed_at datetime.
        now = now.replace(tzinfo=timezone.utc)

    results: list[DrawerDecayResult] = []

    # Build optional where filter.
    where: dict = {}
    if wing:
        where = {"wing": wing}

    # Paginate over the full collection.
    offset = 0
    while True:
        try:
            kwargs: dict = {
                "limit": page_size,
                "offset": offset,
                "include": ["metadatas"],
            }
            if where:
                kwargs["where"] = where
            batch = col.get(**kwargs)
        except Exception:
            logger.warning(
                "decay_pass: batch fetch failed at offset=%d, stopping", offset, exc_info=True
            )
            break

        ids = batch.get("ids") or []
        metas = batch.get("metadatas") or []
        if not ids:
            break

        # Accumulate per-page pending updates; flush once per page to reduce
        # write-lock acquisitions from N to ceil(N/page_size).
        pending_ids: list[str] = []
        pending_metas: list[dict] = []
        # Map drawer_id -> index in results list so we can set written=True
        # after the batched flush.
        pending_result_indices: list[int] = []

        for drawer_id, meta in zip(ids, metas):
            meta = meta or {}
            hall = meta.get("hall")
            w = meta.get("wing", "")
            room = meta.get("room", "")

            # GUARD: skip hall=core — durable identity facts never decay.
            if _is_core_hall(meta):
                old_str = _current_strength(meta)
                results.append(
                    DrawerDecayResult(
                        drawer_id=drawer_id,
                        wing=w,
                        room=room,
                        hall=hall,
                        old_strength=old_str,
                        new_strength=old_str,
                        confidence=_current_confidence(meta),
                        days_since_used=0.0,
                        reason="skipped:core_hall",
                        skipped=True,
                        written=False,
                    )
                )
                continue

            # Determine staleness. Prefer last_used if present; fall back to
            # filed_at / created. If neither is parseable, skip decay for
            # this drawer (unknown staleness) to avoid corrupting fresh data.
            last_used_ts = meta.get("last_used") or meta.get("filed_at") or meta.get("created")
            days = _days_since(last_used_ts, now)
            if days is None:
                old_str = _current_strength(meta)
                results.append(
                    DrawerDecayResult(
                        drawer_id=drawer_id,
                        wing=w,
                        room=room,
                        hall=hall,
                        old_strength=old_str,
                        new_strength=old_str,
                        confidence=_current_confidence(meta),
                        days_since_used=0.0,
                        reason="skipped:no_parseable_timestamp",
                        skipped=True,
                        written=False,
                    )
                )
                continue

            # old_strength is read for reporting only — it is NOT fed into the
            # decay formula. The formula anchors on DRAWER_STRENGTH_DEFAULT so
            # the result is path-independent (idempotent across re-runs).
            old_strength = _current_strength(meta)
            confidence = _current_confidence(meta)
            new_strength = _compute_decay(confidence, days)

            # Invariant check (belt-and-suspenders): new_strength must be ≥ floor
            # and > 0, and the drawer must NOT be deleted.
            assert new_strength >= DRAWER_STRENGTH_FLOOR, (
                f"BUG: decay produced strength {new_strength} < floor {DRAWER_STRENGTH_FLOOR} "
                f"for drawer {drawer_id}"
            )
            assert new_strength > 0, f"BUG: decay produced non-positive strength for {drawer_id}"

            result_idx = len(results)
            results.append(
                DrawerDecayResult(
                    drawer_id=drawer_id,
                    wing=w,
                    room=room,
                    hall=hall,
                    old_strength=old_strength,
                    new_strength=new_strength,
                    confidence=confidence,
                    days_since_used=days,
                    reason="decayed" if new_strength < old_strength - 1e-9 else "no_change",
                    skipped=False,
                    written=False,
                )
            )
            if not dry_run and abs(new_strength - old_strength) > 1e-9:
                pending_ids.append(drawer_id)
                pending_metas.append({"strength": new_strength})
                pending_result_indices.append(result_idx)

        # Flush accumulated updates once per page.
        if pending_ids:
            try:
                col.update(ids=pending_ids, metadatas=pending_metas)
                for idx in pending_result_indices:
                    results[idx].written = True
            except Exception:
                logger.warning(
                    "decay_pass: could not batch-update strengths at offset=%d",
                    offset,
                    exc_info=True,
                )

        offset += len(ids)

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Consolidation pass helpers
# ─────────────────────────────────────────────────────────────────────────────


def _canonical_tiebreak(
    id_a: str,
    meta_a: dict,
    id_b: str,
    meta_b: dict,
) -> str:
    """Return the drawer_id that should be canonical when both are in the same cluster.

    Selection rule (deterministic; order-independent):
      1. Higher ``confidence`` wins.
      2. Tie: longer document text wins (len of the text stored in meta, if present;
         otherwise 0 — callers may pre-populate ``_doc_len`` in the meta dict as an
         optimisation, but the key is not required).
      3. Tie: earlier ``filed_at`` wins (older anchor is more stable).
      4. Tie: lexicographically smaller drawer_id (arbitrary but stable).

    Never returns None; always picks one of the two inputs.
    """
    conf_a = _current_confidence(meta_a)
    conf_b = _current_confidence(meta_b)
    if conf_a != conf_b:
        return id_a if conf_a > conf_b else id_b

    # Tiebreaker 2: longest document text.
    len_a = int(meta_a.get("_doc_len", 0) or 0)
    len_b = int(meta_b.get("_doc_len", 0) or 0)
    if len_a != len_b:
        return id_a if len_a > len_b else id_b

    # Tiebreaker 3: earliest filed_at.
    dt_a = _parse_iso(meta_a.get("filed_at"))
    dt_b = _parse_iso(meta_b.get("filed_at"))
    if dt_a is not None and dt_b is not None and dt_a != dt_b:
        return id_a if dt_a < dt_b else id_b
    if dt_a is not None and dt_b is None:
        return id_a
    if dt_b is not None and dt_a is None:
        return id_b

    # Tiebreaker 4: lexicographically smaller id.
    return id_a if id_a <= id_b else id_b


def _assign_neighbours(
    drawer_id: str,
    meta: dict,
    n_ids: list,
    n_dists: list,
    n_metas: list,
    canonical: dict,
    all_metas: list,
    id_index: dict,
    wing: Optional[str],
) -> None:
    """Update ``canonical`` cluster assignments for all near-neighbours of ``drawer_id``.

    For each neighbour that exceeds ``CONSOLIDATION_SIMILARITY_THRESHOLD`` and
    belongs to the SAME wing as ``drawer_id``, compare confidence values (with
    deterministic tiebreakers) to decide which member is canonical.

    Wing isolation (FIX 2): a neighbour in a *different* wing is never clustered
    with ``drawer_id``, regardless of whether ``wing`` was passed as a filter.
    Cross-wing demotion would suppress a drawer in its own wing's scoped search.

    Canonical selection (FIX 1): within a cluster the member with the HIGHEST
    ``confidence`` is canonical.  When confidences are equal the tiebreaker order is:
    longest document text → earliest ``filed_at`` → lexicographically smallest id.
    This is order-independent — the result does NOT depend on iteration order.

    Extracted from ``consolidation_pass`` to keep that function under the C901
    max-complexity ceiling.
    """
    drawer_wing = meta.get("wing", "")
    for nid, ndist, nmeta in zip(n_ids, n_dists, n_metas):
        if nid == drawer_id:
            continue  # skip self (post-filter)
        nmeta = nmeta or {}
        if _is_core_hall(nmeta):
            continue
        # Wing isolation: ALWAYS require same wing, even when consolidation_pass
        # was called without a wing scope.  Clustering across wings would demote a
        # drawer in wing B purely because wing A has a similar drawer, suppressing
        # it in B-scoped searches.
        if nmeta.get("wing", "") != drawer_wing:
            continue
        # Additionally enforce the explicit wing scope filter if provided.
        if wing and nmeta.get("wing") != wing:
            continue
        similarity = 1.0 - float(ndist)
        if similarity < CONSOLIDATION_SIMILARITY_THRESHOLD:
            continue

        # Determine which drawer should be canonical between drawer_id and nid.
        # We must consider the full cluster chain: if nid is already assigned to a
        # cluster, resolve against that cluster's current canonical.
        if nid in canonical:
            existing_canonical_id = canonical[nid]
            existing_meta = (
                all_metas[id_index[existing_canonical_id]]
                if existing_canonical_id in id_index
                else {}
            )
            # Pick the best canonical among drawer_id, existing_canonical_id.
            winner = _canonical_tiebreak(drawer_id, meta, existing_canonical_id, existing_meta)
            loser_id = existing_canonical_id if winner == drawer_id else drawer_id
            canonical[loser_id] = winner
            # Re-point nid → winner as well (it was already pointing to existing_canonical_id).
            canonical[nid] = winner
            # If winner is drawer_id, it is canonical (point to self or omit; mark loser).
            if winner == drawer_id:
                # drawer_id is now the cluster canonical; ensure it maps to itself.
                canonical[drawer_id] = drawer_id
        else:
            # nid has no cluster yet — compare drawer_id vs nid directly.
            nid_meta = all_metas[id_index[nid]] if nid in id_index else nmeta
            winner = _canonical_tiebreak(drawer_id, meta, nid, nid_meta)
            loser = nid if winner == drawer_id else drawer_id
            canonical[loser] = winner
            if winner == drawer_id:
                canonical[drawer_id] = drawer_id


def _apply_merge_candidate_tunnels(
    canonical: dict,
    all_metas: list,
    id_index: dict,
    wing: Optional[str],
) -> None:
    """Apply WI-4 merge-candidate tunnel hints to the cluster assignment dict.

    Reads all explicit tunnels via ``_list_tunnels_fn`` (monkeypatchable),
    filters to those labeled "merge-candidate", and for each pair demotes
    the lower-confidence member in ``canonical``.

    Operates only on drawers present in ``id_index`` (the current collection
    slice).

    ## Tunnel-path demotion logic

    The tunnel path demotes independently of the vector-path's pre-seeded
    canonical state.  The vector pass pre-seeds every non-core drawer as its
    own canonical key (``canonical[drawer_id] = drawer_id``) before this
    function is called, so the old "loser not in canonical" / "winner not in
    canonical" guards were always False — the tunnel path never fired.

    The corrected approach:

    1. Apply ``_canonical_tiebreak`` to pick winner/loser for the tunnel pair.
    2. Demote the loser unconditionally IF it is not already demoted (i.e.,
       its current canonical is itself or absent — it still considers itself
       canonical).  This is idempotent: if the loser was already demoted by
       the vector pass, its ``canonical`` entry already points to another id
       and we leave it as-is (the demotion is at least as strong).  If the
       vector pass made the loser the canonical of a vector cluster, the
       tunnel signal still overrides it — the tunnel is an explicit human-
       authored merge-candidate signal and takes precedence over a pure
       similarity cluster.
    3. If the vector pass made the winner the loser of another cluster (i.e.,
       ``canonical[winner] != winner``), we do NOT un-demote it — we skip the
       tunnel for this pair since the vector path has already resolved both
       sides into a larger cluster.

    ## Precedence between vector path and tunnel path

    - If the vector path already demoted the loser to some ``canonical_X``,
      the tunnel sees ``canonical[loser] != loser`` and the loser is already
      demoted — no-op on the loser (the demotion stands).
    - If the vector path made the loser canonical of a cluster, the tunnel
      still demotes it (explicit signal beats similarity heuristic).
    - If the vector path demoted the winner (``canonical[winner] != winner``),
      the tunnel is skipped for this pair entirely: both sides are already in
      a larger vector cluster; the tunnel would create a conflicting chain.

    ## Wing isolation

    An unconditional same-wing check guards the tunnel path — exactly like
    ``_assign_neighbours``.  A merge-candidate tunnel linking two drawers in
    different wings NEVER triggers a cross-wing demotion, regardless of
    whether ``wing`` was passed as a scope filter.

    Silently no-ops on any tunnel I/O failure (defensive; consolidation should
    never crash due to unavailable tunnel data).
    """
    try:
        tunnel_response = _list_tunnels_fn()
        if isinstance(tunnel_response, dict):
            tunnel_list = tunnel_response.get("tunnels", [])
        elif isinstance(tunnel_response, list):
            tunnel_list = tunnel_response
        else:
            tunnel_list = []
    except Exception:
        logger.debug("consolidation_pass: merge-candidate tunnel fetch failed", exc_info=True)
        return

    for tunnel in tunnel_list:
        tunnel_label = tunnel.get("label", "") or ""
        if tunnel_label != "merge-candidate":
            continue

        src = tunnel.get("source") or {}
        tgt = tunnel.get("target") or {}
        src_did = src.get("drawer_id")
        tgt_did = tgt.get("drawer_id")

        if not src_did or not tgt_did:
            continue
        if src_did not in id_index or tgt_did not in id_index:
            continue

        src_meta = all_metas[id_index[src_did]]
        tgt_meta = all_metas[id_index[tgt_did]]

        if _is_core_hall(src_meta) or _is_core_hall(tgt_meta):
            continue

        # FIX 3 — unconditional same-wing guard (mirrors _assign_neighbours).
        # A merge-candidate tunnel linking two different-wing drawers must NEVER
        # trigger a cross-wing demotion.  This check is unconditional — it fires
        # even when consolidation_pass was called without a wing scope.
        if src_meta.get("wing", "") != tgt_meta.get("wing", ""):
            continue

        # Additionally enforce the explicit wing scope filter if provided.
        if wing and (src_meta.get("wing") != wing or tgt_meta.get("wing") != wing):
            continue

        # Use the same deterministic tiebreaker as _assign_neighbours.
        winner = _canonical_tiebreak(src_did, src_meta, tgt_did, tgt_meta)
        loser = tgt_did if winner == src_did else src_did

        # FIX 1 — demote independently of the pre-seeded canonical state.
        #
        # Skip the pair entirely if the winner was already demoted by the
        # vector pass (it's in a larger cluster; don't create conflicting chains).
        if canonical.get(winner, winner) != winner:
            continue

        # Demote the loser if it still considers itself canonical (i.e., it is
        # its own canonical entry, which is true for both pre-seeded drawers
        # AND for drawers the vector pass made canonical of a cluster).
        # If the loser is already demoted to someone else, no-op (idempotent).
        current_loser_canonical = canonical.get(loser, loser)
        if current_loser_canonical == loser:
            # Loser thinks it is canonical — the tunnel overrides this.
            canonical[winner] = winner
            canonical[loser] = winner


# ─────────────────────────────────────────────────────────────────────────────
# Consolidation pass
# ─────────────────────────────────────────────────────────────────────────────


def consolidation_pass(
    col,
    *,
    wing: Optional[str] = None,
    dry_run: bool = True,
    page_size: int = 500,
) -> list[DrawerConsolidationResult]:
    """Down-rank near-duplicate drawers without deleting them.

    The pass works in two steps:

    1. **Similarity clustering**: For each non-core drawer, call
       ``col.query`` to find drawers with cosine distance ≤ threshold.
       Two drawers in the same cluster have high semantic overlap.

    2. **Down-rank redundant members**: Within each cluster, the canonical
       representative is chosen deterministically and independent of iteration
       order by the following precedence:

       a. Highest ``confidence`` value wins.
       b. Tie: longest document text (``_doc_len`` metadata key, if present).
       c. Tie: earliest ``filed_at`` timestamp.
       d. Tie: lexicographically smaller drawer_id.

       All non-canonical cluster members are down-ranked to
       ``CONSOLIDATION_DEMOTE_STRENGTH`` — never deleted.

       Wing isolation: clustering is always restricted to drawers in the SAME
       wing.  A near-duplicate in a different wing is never a cluster member,
       even when ``consolidation_pass`` is called without a ``wing`` scope.

    The merge-candidate tunnels created by WI-4's write-back gate (labeled
    "merge-candidate") are an additional consolidation signal: drawers linked
    by a merge-candidate tunnel are treated as a pre-identified cluster, with
    the higher-confidence member kept canonical.

    Args:
        col:       The ``nook_drawers`` ChromaDB collection.
        wing:      Optional wing filter.
        dry_run:   Default True — report only, no writes.
        page_size: Batch fetch size.

    Returns:
        List of ``DrawerConsolidationResult`` — one per drawer examined.

    Structural invariant: No drawer is ever deleted. Only ``strength`` values
    are written via ``col.update``.
    """
    results: list[DrawerConsolidationResult] = []

    # We need the full set of drawers to build clusters. Fetch ids + metadata.
    where: dict = {}
    if wing:
        where = {"wing": wing}

    all_ids: list[str] = []
    all_metas: list[dict] = []
    all_docs: list[str] = []
    all_embs: list = []

    offset = 0
    while True:
        try:
            kwargs: dict = {
                "limit": page_size,
                "offset": offset,
                "include": ["metadatas", "documents", "embeddings"],
            }
            if where:
                kwargs["where"] = where
            batch = col.get(**kwargs)
        except Exception:
            logger.warning(
                "consolidation_pass: batch fetch failed at offset=%d, stopping",
                offset,
                exc_info=True,
            )
            break

        ids = batch.get("ids") or []
        metas = batch.get("metadatas") or []
        docs = batch.get("documents") or []
        raw_embs = batch.get("embeddings")
        embs = list(raw_embs) if raw_embs is not None else []
        if not ids:
            break

        for i, (did, meta, doc) in enumerate(zip(ids, metas, docs)):
            all_ids.append(did)
            all_metas.append(meta or {})
            all_docs.append(doc or "")
            all_embs.append(embs[i] if i < len(embs) else None)

        offset += len(ids)

    if not all_ids:
        return results

    # Build clusters using vector similarity: for each candidate drawer,
    # query the collection for its nearest neighbours. Because the cosine
    # distance call is expensive at scale, we cap each neighbourhood query
    # at n_results=10, and we skip hall=core drawers.
    #
    # Cluster assignments: the first time we see a drawer as a query result
    # it may become canonical; subsequent appearances mark it as redundant.
    # We track via a union-find structure (simple dict mapping id → canonical_id).
    canonical: dict[str, str] = {}  # drawer_id -> canonical_id for its cluster

    # Build a fast lookup: drawer_id → its position in all_ids for meta access.
    _id_index: dict[str, int] = {did: i for i, did in enumerate(all_ids)}

    for drawer_id, meta in zip(all_ids, all_metas):
        # Skip core hall — excluded from consolidation.
        if _is_core_hall(meta):
            continue

        # Already assigned to a cluster (redundant member).
        if drawer_id in canonical:
            continue

        # This drawer becomes its own canonical representative by default.
        canonical[drawer_id] = drawer_id

        # Retrieve pre-fetched document text (folded into the batch scan above
        # to avoid N per-drawer col.get round-trips).
        doc_text = all_docs[_id_index[drawer_id]]

        if not doc_text:
            # No text content — can't compute similarity; skip.
            continue

        # Populate _doc_len in the working meta copy so _canonical_tiebreak can
        # use document-length as a tiebreaker.  This mutates the in-memory dict
        # in all_metas; the Chroma store is never touched.
        all_metas[_id_index[drawer_id]]["_doc_len"] = len(doc_text)

        # Query for near-neighbours WITHOUT filtering to self. Self will appear
        # in results and is excluded by the post-filter `if nid == drawer_id`.
        # n_results is capped at total collection size; +1 accounts for self.
        # Use the stored embedding (fetched in the batch scan) to avoid
        # re-running N ONNX forward passes on documents already vectorized.
        n_query = min(11, max(1, len(all_ids)))
        stored_emb = all_embs[_id_index[drawer_id]]
        try:
            if stored_emb is not None:
                neighbours = col.query(
                    query_embeddings=[stored_emb],
                    n_results=n_query,
                    include=["metadatas", "distances"],
                )
            else:
                # Fallback when stored embedding is unavailable.
                neighbours = col.query(
                    query_texts=[doc_text],
                    n_results=n_query,
                    include=["metadatas", "distances"],
                )
        except Exception:
            # Can't get neighbours — skip this drawer.
            logger.debug(
                "consolidation_pass: query failed for %s, skipping", drawer_id, exc_info=True
            )
            continue

        n_ids = (neighbours.get("ids") or [[]])[0] or []
        n_dists = (neighbours.get("distances") or [[]])[0] or []
        n_metas = (neighbours.get("metadatas") or [[]])[0] or []

        _assign_neighbours(
            drawer_id, meta, n_ids, n_dists, n_metas, canonical, all_metas, _id_index, wing
        )

    # ── Merge-candidate tunnel path ───────────────────────────────────────────
    # WI-4 write-back creates explicit tunnels labeled "merge-candidate" for
    # pairs the similarity gate flagged as near-matches. Process these as an
    # additional cluster source — extracted to a helper to keep this function
    # under the C901 complexity ceiling.
    _apply_merge_candidate_tunnels(canonical, all_metas, _id_index, wing)

    # Now build results and apply writes where needed.
    # Accumulate pending demotion writes and flush in one batched col.update
    # to reduce write-lock acquisitions from N to 1.
    pending_ids: list[str] = []
    pending_metas: list[dict] = []
    pending_result_indices: list[int] = []

    for drawer_id, meta in zip(all_ids, all_metas):
        hall = meta.get("hall")
        w = meta.get("wing", "")
        room = meta.get("room", "")
        old_strength = _current_strength(meta)

        # Core hall: skip, report unchanged.
        if _is_core_hall(meta):
            results.append(
                DrawerConsolidationResult(
                    drawer_id=drawer_id,
                    wing=w,
                    room=room,
                    hall=hall,
                    old_strength=old_strength,
                    new_strength=old_strength,
                    canonical_id=None,
                    reason="skipped:core_hall",
                    skipped=True,
                    written=False,
                )
            )
            continue

        # Resolve to the ROOT of the union-find chain so canonical_id in the
        # result always names the true cluster root (FIX 3 / WI-6 polish).
        # In a tunnel chain C→B where B→A, canonical[C]=B and canonical[B]=A;
        # we follow until self-canonical to report A, not the intermediate B.
        _raw_canonical = canonical.get(drawer_id, drawer_id)
        cluster_canonical = _raw_canonical
        _visited: set = set()
        while True:
            _next = canonical.get(cluster_canonical, cluster_canonical)
            if _next == cluster_canonical or _next in _visited:
                break
            _visited.add(cluster_canonical)
            cluster_canonical = _next
        is_canonical = cluster_canonical == drawer_id

        if is_canonical:
            results.append(
                DrawerConsolidationResult(
                    drawer_id=drawer_id,
                    wing=w,
                    room=room,
                    hall=hall,
                    old_strength=old_strength,
                    new_strength=old_strength,
                    canonical_id=None,  # is canonical
                    reason="canonical",
                    skipped=False,
                    written=False,
                )
            )
            continue

        # This drawer is a redundant near-duplicate — down-rank it.
        # Floor enforcement: CONSOLIDATION_DEMOTE_STRENGTH > DRAWER_STRENGTH_FLOOR.
        new_strength = max(DRAWER_STRENGTH_FLOOR, CONSOLIDATION_DEMOTE_STRENGTH)

        # Structural invariant: never delete.
        assert new_strength >= DRAWER_STRENGTH_FLOOR, (
            f"BUG: consolidation produced strength {new_strength} < floor for {drawer_id}"
        )
        assert new_strength > 0, (
            f"BUG: consolidation produced non-positive strength for {drawer_id}"
        )

        result_idx = len(results)
        results.append(
            DrawerConsolidationResult(
                drawer_id=drawer_id,
                wing=w,
                room=room,
                hall=hall,
                old_strength=old_strength,
                new_strength=new_strength,
                canonical_id=cluster_canonical,
                reason="demoted:near_duplicate",
                skipped=False,
                written=False,
            )
        )
        if not dry_run and abs(new_strength - old_strength) > 1e-9:
            pending_ids.append(drawer_id)
            pending_metas.append({"strength": new_strength})
            pending_result_indices.append(result_idx)

    # Flush accumulated demotion updates in a single batched write.
    if pending_ids:
        try:
            col.update(ids=pending_ids, metadatas=pending_metas)
            for idx in pending_result_indices:
                results[idx].written = True
        except Exception:
            logger.warning(
                "consolidation_pass: could not batch-update demotion strengths",
                exc_info=True,
            )

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Summarise pass results (CLI helper)
# ─────────────────────────────────────────────────────────────────────────────


def summarise_decay_results(results: list[DrawerDecayResult]) -> dict:
    """Produce a summary dict from a decay_pass result list.

    Returns:
        {
            "total": int,
            "skipped_core": int,
            "skipped_no_timestamp": int,
            "decayed": int,
            "no_change": int,
            "written": int,
            "avg_old_strength": float | None,
            "avg_new_strength": float | None,
        }
    """
    total = len(results)
    skipped_core = sum(1 for r in results if r.reason == "skipped:core_hall")
    skipped_ts = sum(1 for r in results if r.reason == "skipped:no_parseable_timestamp")
    decayed = sum(1 for r in results if r.reason == "decayed")
    no_change = sum(1 for r in results if r.reason == "no_change")
    written = sum(1 for r in results if r.written)

    active = [r for r in results if not r.skipped]
    avg_old = sum(r.old_strength for r in active) / len(active) if active else None
    avg_new = sum(r.new_strength for r in active) / len(active) if active else None

    return {
        "total": total,
        "skipped_core": skipped_core,
        "skipped_no_timestamp": skipped_ts,
        "decayed": decayed,
        "no_change": no_change,
        "written": written,
        "avg_old_strength": round(avg_old, 4) if avg_old is not None else None,
        "avg_new_strength": round(avg_new, 4) if avg_new is not None else None,
    }


def summarise_consolidation_results(results: list[DrawerConsolidationResult]) -> dict:
    """Produce a summary dict from a consolidation_pass result list."""
    total = len(results)
    skipped_core = sum(1 for r in results if r.reason == "skipped:core_hall")
    canonical_count = sum(1 for r in results if r.reason == "canonical")
    demoted = sum(1 for r in results if r.reason == "demoted:near_duplicate")
    written = sum(1 for r in results if r.written)

    return {
        "total": total,
        "skipped_core": skipped_core,
        "canonical": canonical_count,
        "demoted": demoted,
        "written": written,
    }


__all__ = [
    "DRAWER_STRENGTH_DEFAULT",
    "DRAWER_STRENGTH_FLOOR",
    "BASE_DECAY_RATE",
    "HIGH_CONFIDENCE_PROTECTION",
    "CONSOLIDATION_SIMILARITY_THRESHOLD",
    "CONSOLIDATION_DEMOTE_STRENGTH",
    "CORE_HALL_NAME",
    "DrawerDecayResult",
    "DrawerConsolidationResult",
    "decay_pass",
    "consolidation_pass",
    "summarise_decay_results",
    "summarise_consolidation_results",
]
