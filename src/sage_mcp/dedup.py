"""
dedup.py — Detect and remove near-duplicate drawers; write-back gate
=====================================================================

Two responsibilities:

1. **Batch deduplicator** — when the same files are mined multiple
   times, near-identical drawers accumulate. This module finds drawers
   from the same source_file that are too similar (cosine distance <
   threshold), keeps the longest/richest version, and deletes the rest.

2. **Write-back gate** (``write_back_gate``) — before ``aidev-keeper``
   stores a session-end write-back drawer, the gate runs a semantic
   near-match check and returns a decision:

   - ``STORE``           — no near-match; safe to store.
   - ``SKIP``            — exact/near-duplicate exists (similarity ≥
                           ``skip_threshold``, default 0.90); suppress.
   - ``MERGE-CANDIDATE`` — near-match in the 0.85–0.90 band; surface for
                           consolidation but do not store automatically.

   The gate scrubs secrets from the content before checking (CLAUDE.md §9
   nook_* reserved for aidev-keeper / PRD §13 bounded-autonomy:
   written drawers can surface in Tier-0/retrieval). It delegates the
   similarity query to a caller-supplied ``query_fn`` so it can reuse the
   MCP server's collection path (``tool_check_duplicate``) without
   importing mcp_server — keeping the dependency graph clean and the
   module independently testable.

   **Use ``make_query_fn`` (see below) to wrap ``tool_check_duplicate``.**
   The wrapper raises when the vector backend reports ``vector_disabled``,
   so the gate deterministically sets ``dedup_ran=False`` rather than
   silently treating an empty match list as "no duplicates found".

   **This module does NOT write to the nook.** The actual write stays
   with ``aidev-keeper`` (CLAUDE.md §9 nook_* reserved for
   aidev-keeper; single-writer). The gate is a pure decision helper.

No API calls — uses ChromaDB's built-in embedding similarity.

Usage (standalone batch dedup):
    python -m sage_mcp.dedup                          # dedup all
    python -m sage_mcp.dedup --dry-run                # preview only
    python -m sage_mcp.dedup --threshold 0.10         # stricter (near-identical only)
    python -m sage_mcp.dedup --threshold 0.35         # looser (catches paraphrased content)
    python -m sage_mcp.dedup --wing my_project        # scope to one wing
    python -m sage_mcp.dedup --stats                  # stats only
    python -m sage_mcp.dedup --source "my_project"    # filter by source

Usage (from CLI):
    sage dedup [--dry-run] [--threshold 0.15] [--stats]
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .backends.chroma import ChromaBackend
from .secret_scrub import scrub_secrets

logger = logging.getLogger(__name__)


# ── Write-back gate ───────────────────────────────────────────────────────────


class GateDecision(str, Enum):
    """Decision returned by ``write_back_gate``.

    Inherits ``str`` so the value is JSON-serialisable and can be compared
    directly to the string literals ``"STORE"``, ``"SKIP"``,
    ``"MERGE-CANDIDATE"`` without an extra ``.value`` dereference.
    """

    STORE = "STORE"
    SKIP = "SKIP"
    MERGE_CANDIDATE = "MERGE-CANDIDATE"


@dataclass
class WriteBackDecision:
    """Result of ``write_back_gate``.

    Attributes:
        decision:       ``STORE`` | ``SKIP`` | ``MERGE-CANDIDATE``.
        scrubbed:       Write-back content after secret scrubbing.
        top_match_id:   Nook drawer ID of the nearest existing match
                        (``None`` when decision is ``STORE``, and also
                        ``None`` when a match dict lacks an ``"id"`` key —
                        guard with ``if top_match_id`` before using).
        top_similarity: Cosine similarity to that match (0.0 when
                        decision is ``STORE``).
        matches:        Full list of near-matches from the similarity query.
        dedup_ran:      ``True`` when the similarity query completed normally;
                        ``False`` when the vector backend was unavailable or
                        ``query_fn`` raised — meaning dedup could NOT check
                        for duplicates.  A ``False`` value with decision
                        ``STORE`` indicates a degraded write, not a confirmed
                        novel drawer.
    """

    decision: GateDecision
    scrubbed: str
    top_match_id: str | None = None
    top_similarity: float = 0.0
    matches: list[dict[str, Any]] = field(default_factory=list)
    dedup_ran: bool = True


# Cosine SIMILARITY thresholds for the write-back gate.
# ≥ SKIP_THRESHOLD   → SKIP  (near-exact duplicate; restatement)
# ≥ MERGE_THRESHOLD  → MERGE-CANDIDATE (near-match; surface for consolidation)
# < MERGE_THRESHOLD  → STORE (novel content)
SKIP_THRESHOLD: float = 0.90
MERGE_THRESHOLD: float = 0.85


class VectorDisabledError(RuntimeError):
    """Raised by ``make_query_fn`` when the vector backend is disabled.

    The write-back gate catches ``Exception`` and sets ``dedup_ran=False``
    on any query failure.  Raising this specific class (rather than
    returning an empty list) makes the degraded path deterministic and
    distinguishable from "genuinely no near-matches found" — resolving
    WI-4 I#3: silent dedup no-op when ``tool_check_duplicate`` returns
    ``vector_disabled: true``.
    """


def make_query_fn(check_duplicate_callable) -> Callable[[str, int], list[dict[str, Any]]]:
    """Wrap a ``tool_check_duplicate``-shaped callable as a gate-safe ``query_fn``.

    The returned function:
    - Calls ``check_duplicate_callable(content=text, threshold=0.0)``
      (threshold=0.0 returns ALL near-results regardless of score, so the
      gate's own threshold logic governs filtering — not the tool's).
    - **Raises ``VectorDisabledError``** when the result carries
      ``vector_disabled: true``, so ``write_back_gate`` sets
      ``dedup_ran=False`` instead of silently returning STORE on an empty
      match list that merely reflects a disabled backend.
    - Returns the ``matches`` list from a healthy result.

    Usage (inside aidev-keeper or tests)::

        query_fn = make_query_fn(tool_check_duplicate)
        decision = write_back_gate(content, query_fn)

    ``check_duplicate_callable`` must accept keyword arguments
    ``content`` and ``threshold`` and return a dict with at least
    ``{"matches": list, ...}`` or ``{"vector_disabled": True, ...}``.
    This matches the shape of ``mcp_server.tool_check_duplicate``.
    """

    def _query_fn(text: str, n_results: int) -> list[dict[str, Any]]:
        result = check_duplicate_callable(content=text, threshold=0.0)
        if not isinstance(result, dict):
            return []
        if result.get("vector_disabled"):
            raise VectorDisabledError(
                f"vector backend disabled: {result.get('vector_disabled_reason', 'unknown')}"
            )
        return result.get("matches", [])

    return _query_fn


def write_back_gate(
    content: str,
    query_fn: Callable[[str, int], list[dict[str, Any]]],
    *,
    skip_threshold: float = SKIP_THRESHOLD,
    merge_threshold: float = MERGE_THRESHOLD,
) -> WriteBackDecision:
    """Gate a write-back drawer before it is stored in the nook.

    The gate:
    1. Scrubs secrets from ``content`` (``secret_scrub.scrub_secrets``).
    2. Calls ``query_fn(scrubbed_content, n_results=5)`` to find the
       nearest existing drawers.  ``query_fn`` must return a list of dicts,
       each with at least ``{"id": str, "similarity": float}``; this is the
       shape returned by ``tool_check_duplicate`` in ``mcp_server.py``.
    3. Returns a ``WriteBackDecision`` with:

       - ``STORE``           if no match has similarity ≥ ``merge_threshold``
       - ``MERGE-CANDIDATE`` if the top match has
         ``merge_threshold`` ≤ similarity < ``skip_threshold``
       - ``SKIP``            if the top match has similarity ≥ ``skip_threshold``

    **This function does NOT write to the nook.**  Calling code
    (``aidev-keeper``) checks the decision and performs the actual write
    (CLAUDE.md §9 nook_* reserved for aidev-keeper; single-writer).

    Args:
        content:         Raw write-back text (may contain secrets; will be
                         scrubbed before the similarity check).
        query_fn:        Callable ``(text, n_results) → list[match_dict]``.
                         Reuse the ``tool_check_duplicate`` similarity path
                         from ``mcp_server``; the dict shape is
                         ``{"id": str, "similarity": float, ...}``.
                         **Contract:** when the vector backend is disabled or
                         degraded (e.g. ``tool_check_duplicate`` returns
                         ``{"vector_disabled": True, ...}``), the wrapper
                         **must raise** so the gate can distinguish degraded
                         from genuinely novel.  A silent empty-list return is
                         indistinguishable from "no near-matches found".
        skip_threshold:  Similarity at or above which content is a duplicate
                         (default 0.90, matching ``nook_check_duplicate``).
        merge_threshold: Lower bound of the merge-candidate band (default
                         0.85).
                         **Degenerate case:** when ``merge_threshold ==
                         skip_threshold`` the MERGE-CANDIDATE band collapses
                         to zero width; every match is either SKIP or STORE
                         (valid but unusual — no MERGE-CANDIDATE is ever
                         returned).

    Returns:
        ``WriteBackDecision`` — never raises; on query failure returns
        ``STORE`` with ``dedup_ran=False`` (fail-open: prefer a false
        negative over blocking all writes when the vector backend is
        unavailable).  Callers should treat ``dedup_ran=False`` as a
        degraded write and log/flag accordingly.
    """
    if merge_threshold > skip_threshold:
        raise ValueError(
            f"merge_threshold ({merge_threshold}) must be ≤ skip_threshold ({skip_threshold})"
        )

    scrubbed = scrub_secrets(content)

    try:
        matches = query_fn(scrubbed, 5)
    except Exception:
        # Vector backend unavailable — fail open so the Keeper can still
        # write.  Mark dedup_ran=False so the caller knows the write is
        # unverified (not confirmed novel) and can flag / log accordingly.
        logger.warning(
            "write_back_gate: dedup query failed (vector backend degraded or unavailable); "
            "storing without dedup check — near-duplicates may accumulate."
        )
        return WriteBackDecision(
            decision=GateDecision.STORE,
            scrubbed=scrubbed,
            dedup_ran=False,
        )

    if not matches:
        return WriteBackDecision(decision=GateDecision.STORE, scrubbed=scrubbed)

    top = max(matches, key=lambda m: m.get("similarity", 0.0))
    top_sim = float(top.get("similarity", 0.0))
    top_id = top.get("id")

    if top_sim >= skip_threshold:
        decision = GateDecision.SKIP
    elif top_sim >= merge_threshold:
        decision = GateDecision.MERGE_CANDIDATE
    else:
        decision = GateDecision.STORE

    return WriteBackDecision(
        decision=decision,
        scrubbed=scrubbed,
        top_match_id=top_id if decision != GateDecision.STORE else None,
        top_similarity=top_sim if decision != GateDecision.STORE else 0.0,
        matches=matches,
    )


# ── Write-back categories and episodic record shape ───────────────────────────


class WriteBackCategory(str, Enum):
    """The four structured write-back categories (PRD §8).

    Routing (performed by ``aidev-keeper``, not this module):
    - DECISION       → ``decisions`` hall of the active wing.
    - SOLVED_PROBLEM → episodic record drawer (see ``EpisodicRecord``).
    - USER_FACT      → ``facts`` hall of the ACTIVE wing (v1; WI-5 will
                       introduce the ``Personal`` wing core/detail split
                       and re-route; route to unregistered wing avoided).
    - SKILL          → write/update ``SKILL.md`` on disk, then update the
                       registry drawer in ``skill_registry``.
    """

    DECISION = "decision"
    SOLVED_PROBLEM = "solved_problem"
    USER_FACT = "user_fact"
    SKILL = "skill"


@dataclass
class EpisodicRecord:
    """Drawer shape for a ``SOLVED_PROBLEM`` write-back (PRD §8 / Hermes pattern).

    All four fields are required.  ``what_failed`` may be an empty string
    when nothing failed, but must be present so the schema is consistent
    across retrieval.

    The Keeper serialises this to a nook drawer document as::

        task: <task>
        what_tried: <what_tried>
        what_worked: <what_worked>
        what_failed: <what_failed>

    The flat text format makes the drawer retrievable via BM25/FTS5 as
    well as vector similarity (both are hybrid-indexed).
    """

    task: str
    what_tried: str
    what_worked: str
    what_failed: str

    def to_document(self) -> str:
        """Serialise to a nook drawer document string."""
        return (
            f"task: {self.task}\n"
            f"what_tried: {self.what_tried}\n"
            f"what_worked: {self.what_worked}\n"
            f"what_failed: {self.what_failed}"
        )

    @classmethod
    def from_document(cls, text: str) -> "EpisodicRecord":
        """Parse a drawer document string back into an ``EpisodicRecord``.

        Tolerates missing fields (returns empty string for each).

        **Note:** uses ``line.partition(": ")`` which takes only the first
        ``": "`` occurrence on each line.  ``to_document`` never emits
        multi-line field values (each field is a single-line ``key: value``
        pair), so continuation lines are not a concern in practice — but a
        manually crafted document with embedded newlines in a value would
        have those continuation lines silently dropped.
        """
        fields: dict[str, str] = {}
        for line in text.splitlines():
            if ": " in line:
                key, _, value = line.partition(": ")
                fields[key.strip()] = value.strip()
        return cls(
            task=fields.get("task", ""),
            what_tried=fields.get("what_tried", ""),
            what_worked=fields.get("what_worked", ""),
            what_failed=fields.get("what_failed", ""),
        )


# ── Batch deduplicator ───────────────────────────────────────────────────────

COLLECTION_NAME = "nook_drawers"
# Cosine DISTANCE threshold (not similarity). Lower = stricter.
# 0.15 = ~85% cosine similarity — catches near-identical chunks.
# For looser dedup of paraphrased content, try 0.3–0.4.
DEFAULT_THRESHOLD = 0.15
MIN_DRAWERS_TO_CHECK = 5


def _get_nook_path():
    """Resolve nook path from config."""
    try:
        from .config import SageConfig

        return SageConfig().nook_path
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".sage", "nook")


def get_source_groups(col, min_count=MIN_DRAWERS_TO_CHECK, source_pattern=None, wing=None):
    """Group drawers by source_file, return groups with min_count+ entries.

    If wing is specified, only considers drawers in that wing. This catches
    cross-wing duplicates when the same source was mined into multiple wings.
    """
    total = col.count()
    groups = defaultdict(list)

    offset = 0
    batch_size = 1000
    while offset < total:
        kwargs = {"limit": batch_size, "offset": offset, "include": ["metadatas"]}
        if wing:
            kwargs["where"] = {"wing": wing}
        batch = col.get(**kwargs)
        if not batch["ids"]:
            break
        for did, meta in zip(batch["ids"], batch["metadatas"]):
            src = meta.get("source_file", "unknown")
            if source_pattern and source_pattern.lower() not in src.lower():
                continue
            groups[src].append(did)
        offset += len(batch["ids"])

    return {src: ids for src, ids in groups.items() if len(ids) >= min_count}


def dedup_source_group(col, drawer_ids, threshold=DEFAULT_THRESHOLD, dry_run=True):
    """Dedup drawers within one source_file group.

    Greedy: sort by doc length (longest first), keep if not too similar
    to any already-kept drawer. Returns (kept_ids, deleted_ids).
    """
    data = col.get(ids=drawer_ids, include=["documents", "metadatas"])
    items = list(zip(data["ids"], data["documents"], data["metadatas"]))
    items.sort(key=lambda x: len(x[1] or ""), reverse=True)

    kept = []
    to_delete = []

    for did, doc, _meta in items:
        if not doc or len(doc) < 20:
            to_delete.append(did)
            continue

        if not kept:
            kept.append((did, doc))
            continue

        try:
            results = col.query(
                query_texts=[doc],
                n_results=min(len(kept), 5),
                include=["distances"],
            )
            dists = results["distances"][0] if results["distances"] else []
            kept_ids_set = {k[0] for k in kept}

            is_dup = False
            for rid, dist in zip(results["ids"][0], dists):
                if rid in kept_ids_set and dist < threshold:
                    is_dup = True
                    break

            if is_dup:
                to_delete.append(did)
            else:
                kept.append((did, doc))
        except Exception:
            kept.append((did, doc))

    if to_delete and not dry_run:
        for i in range(0, len(to_delete), 500):
            col.delete(ids=to_delete[i : i + 500])

    return [k[0] for k in kept], to_delete


def show_stats(nook_path=None):
    """Show duplication statistics without making changes."""
    nook_path = nook_path or _get_nook_path()
    col = ChromaBackend().get_collection(nook_path, COLLECTION_NAME)

    groups = get_source_groups(col)

    total_drawers = sum(len(ids) for ids in groups.values())
    print(f"\n  Sources with {MIN_DRAWERS_TO_CHECK}+ drawers: {len(groups)}")
    print(f"  Total drawers in those sources: {total_drawers:,}")

    print("\n  Top 15 by drawer count:")
    sorted_groups = sorted(groups.items(), key=lambda x: len(x[1]), reverse=True)
    for src, ids in sorted_groups[:15]:
        print(f"    {len(ids):4d}  {src[:65]}")

    estimated_dups = sum(int(len(ids) * 0.4) for ids in groups.values() if len(ids) > 20)
    print(f"\n  Estimated duplicates (groups > 20): ~{estimated_dups:,}")


def dedup_nook(
    nook_path=None,
    threshold=DEFAULT_THRESHOLD,
    dry_run=True,
    source_pattern=None,
    min_count=MIN_DRAWERS_TO_CHECK,
    wing=None,
):
    """Main entry point: deduplicate near-identical drawers across the nook."""
    nook_path = nook_path or _get_nook_path()

    print(f"\n{'=' * 55}")
    print("  sage Deduplicator")
    print(f"{'=' * 55}")

    col = ChromaBackend().get_collection(nook_path, COLLECTION_NAME)

    print(f"  Nook: {nook_path}")
    print(f"  Drawers: {col.count():,}")
    print(f"  Threshold: {threshold}")
    print(f"  Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"{'─' * 55}")

    if wing:
        print(f"  Wing: {wing}")
    groups = get_source_groups(col, min_count, source_pattern, wing=wing)
    print(f"\n  Sources to check: {len(groups)}")

    t0 = time.time()
    total_kept = 0
    total_deleted = 0

    sorted_groups = sorted(groups.items(), key=lambda x: len(x[1]), reverse=True)

    for i, (src, drawer_ids) in enumerate(sorted_groups):
        kept, deleted = dedup_source_group(col, drawer_ids, threshold, dry_run)
        total_kept += len(kept)
        total_deleted += len(deleted)

        if deleted:
            print(
                f"  [{i + 1:3d}/{len(groups)}] "
                f"{src[:50]:50s} {len(drawer_ids):4d} → {len(kept):4d}  "
                f"(-{len(deleted)})"
            )

    elapsed = time.time() - t0

    print(f"\n{'─' * 55}")
    print(f"  Done in {elapsed:.1f}s")
    print(
        f"  Drawers: {total_kept + total_deleted:,} → {total_kept:,}  (-{total_deleted:,} removed)"
    )
    print(f"  Nook after: {col.count():,} drawers")

    if dry_run:
        print("\n  [DRY RUN] No changes written. Re-run without --dry-run to apply.")

    print(f"{'=' * 55}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deduplicate near-identical drawers")
    parser.add_argument("--nook", default=None, help="Nook directory path")
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Cosine distance threshold (default: {DEFAULT_THRESHOLD})",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without deleting")
    parser.add_argument("--stats", action="store_true", help="Show stats only")
    parser.add_argument("--wing", default=None, help="Scope dedup to a single wing")
    parser.add_argument("--source", default=None, help="Filter by source file pattern")
    args = parser.parse_args()

    path = os.path.expanduser(args.nook) if args.nook else None

    if args.stats:
        show_stats(nook_path=path)
    else:
        dedup_nook(
            nook_path=path,
            threshold=args.threshold,
            dry_run=args.dry_run,
            source_pattern=args.source,
            wing=args.wing,
        )
