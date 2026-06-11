/**
 * Slot ledger — the id→slot assignment engine.
 *
 * Contract (ADR-0005):
 * - Each node id receives a `slot` at first-sight; the mapping is permanent.
 * - A deleted id's slot is NEVER reclaimed or repacked — it becomes a gap (ruin).
 * - New ids take `nextSlot`, which only ever increases.
 * - This module is pure: no I/O, no side effects, no global state.
 *   Persistence beside the model (Phase 3) hands this ledger a prior snapshot
 *   and gets back an updated snapshot; the pure logic here is the same either way.
 *
 * WHERE: src/sage_mcp/estate/web/src/layout/ledger.ts
 */

/** Immutable snapshot of id→slot assignments. */
export interface Ledger {
  /** Stable mapping from node id to its assigned slot. */
  readonly slots: Readonly<Record<string, number>>;
  /**
   * The next slot number to hand out.
   * Equal to (max assigned slot + 1), or 0 when empty.
   */
  readonly nextSlot: number;
}

/** An empty ledger — the starting point when no prior snapshot exists. */
export const EMPTY_LEDGER: Ledger = { slots: {}, nextSlot: 0 };

/**
 * Build (or rebuild) a ledger from an ordered list of ids.
 *
 * Each id receives its slot in the order it appears. If the same id appears
 * more than once only the first occurrence counts (idempotent on duplicates).
 *
 * This is the function to call when constructing a fresh ledger from a list
 * of ids that already carry stable ordering (e.g. the `slot` fields already
 * present in the model). When no prior ordering exists, pass the ids in their
 * desired stable order.
 */
export function buildLedger(ids: readonly string[]): Ledger {
  const slots: Record<string, number> = {};
  let nextSlot = 0;
  for (const id of ids) {
    if (!(id in slots)) {
      slots[id] = nextSlot++;
    }
  }
  return { slots, nextSlot };
}

/**
 * Extend an existing ledger with new ids not yet seen.
 *
 * Ids already in `ledger.slots` keep their existing slot — unchanged.
 * New ids are appended at `ledger.nextSlot` and up.
 * Returns a new Ledger (does not mutate the argument).
 */
export function addIds(ledger: Ledger, ids: readonly string[]): Ledger {
  const slots: Record<string, number> = { ...ledger.slots };
  let nextSlot = ledger.nextSlot;
  for (const id of ids) {
    if (!(id in slots)) {
      slots[id] = nextSlot++;
    }
  }
  return { slots, nextSlot };
}

/**
 * Look up a slot for a known id.
 *
 * Returns `undefined` when the id is not in the ledger (never-seen id).
 * Callers that encounter `undefined` should call `addIds` first.
 */
export function slotOf(ledger: Ledger, id: string): number | undefined {
  return ledger.slots[id];
}

/**
 * Given a ledger and the set of ids currently present in the model,
 * return the slots whose ids have been deleted (ruin slots).
 *
 * Ruin slots are slots that were assigned (appear in `ledger.slots`) but whose
 * ids are NOT in `activeIds`. The layout engine renders these as empty-closet
 * glyphs / gap entries in the scene graph.
 *
 * The returned array is sorted ascending for deterministic ordering.
 */
export function ruinSlots(
  ledger: Ledger,
  activeIds: ReadonlySet<string>,
): readonly number[] {
  const ruins: number[] = [];
  for (const [id, slot] of Object.entries(ledger.slots)) {
    if (!activeIds.has(id)) {
      ruins.push(slot);
    }
  }
  return ruins.sort((a, b) => a - b);
}

/**
 * Merge a ledger built from the current model into a prior ledger.
 *
 * The prior ledger is authoritative for any id it already knows — its slot
 * assignments take precedence. New ids (present in `fresh` but not in `prior`)
 * are appended starting at `max(prior.nextSlot, fresh.nextSlot)`.
 *
 * This is the Phase-3 persistence path: load the persisted ledger as `prior`,
 * build a fresh ledger from the current model ids, then merge.
 */
export function mergeLedgers(prior: Ledger, fresh: Ledger): Ledger {
  const slots: Record<string, number> = { ...prior.slots };
  let nextSlot = prior.nextSlot;
  for (const [id, freshSlot] of Object.entries(fresh.slots)) {
    if (!(id in slots)) {
      // New id — give it the next slot from the prior ledger's frontier,
      // ignoring the fresh slot (which was assigned without ledger history).
      slots[id] = nextSlot++;
      void freshSlot; // fresh slot is discarded in favour of prior frontier
    }
  }
  return { slots, nextSlot };
}
