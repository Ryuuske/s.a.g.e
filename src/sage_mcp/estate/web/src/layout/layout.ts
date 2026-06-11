/**
 * Layout engine — pure function: EstateModel + DesignTokens → SceneGraph.
 *
 * Contract (Phase 2a invariants, ADR-0005):
 * - Same model ⇒ byte-identical scene graph (deterministic, pure — no Date,
 *   no Math.random, no iteration over unordered data structures).
 * - Placement reads `slot` from the model / ledger — NEVER sorts-by-id.
 * - Deleted ids surface as `ruins` in the scene graph (empty-closet glyph).
 * - No SVG, no DOM, no I/O — render-agnostic output only.
 *
 * Layout rules (plan §5):
 * - Palace footprint scales with wing count; each wing is a hall block whose
 *   room cells scale with room count; drawer glyphs per room are bucketed.
 * - Workshop: one agent-room per agent, bucketed into family wings.
 * - Grounds: one courtyard plot per repo, tiled to fit N.
 * - The three zones (palace, workshop, grounds) are laid out left-to-right on
 *   the iso grid at fixed X offsets, with internal items placed by slot.
 *
 * WHERE: src/sage_mcp/estate/web/src/layout/layout.ts
 */

import type {
  Agent,
  DesignTokens,
  EstateModel,
  NookBuilding,
  PlacedNode,
  Plot,
  Room,
  RuinNode,
  SceneGraph,
  Wing,
  WorkshopBuilding,
} from "../model/types";
import {
  EMPTY_LEDGER,
  buildLedger,
  mergeLedgers,
  ruinSlots,
  slotOf,
} from "./ledger";
import type { Ledger } from "./ledger";

// ---------------------------------------------------------------------------
// Token defaults
// ---------------------------------------------------------------------------

const DEFAULT_BASE_CELL = 1;
const DEFAULT_DRAWER_PAGE_SIZE = 10;
const DEFAULT_ROOMS_PER_ROW = 4;
const DEFAULT_AGENTS_PER_ROW = 4;
const DEFAULT_PLOTS_PER_ROW = 4;

// Zone X offsets on the iso grid (palace left, workshop centre, grounds right).
// These are fixed abstract-unit offsets so the three buildings never overlap.
const PALACE_ORIGIN_X = 0;
const WORKSHOP_ORIGIN_X = 32;
const GROUNDS_ORIGIN_X = 64;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function resolveTokens(tokens: DesignTokens): Required<{
  baseCell: number;
  drawerPageSize: number;
  roomsPerRow: number;
  agentsPerRow: number;
  plotsPerRow: number;
}> {
  const rawBaseCell =
    typeof tokens["baseCell"] === "number"
      ? tokens["baseCell"]
      : DEFAULT_BASE_CELL;
  const rawDrawerPageSize =
    typeof tokens["drawerPageSize"] === "number"
      ? tokens["drawerPageSize"]
      : DEFAULT_DRAWER_PAGE_SIZE;
  const rawRoomsPerRow =
    typeof tokens["roomsPerRow"] === "number"
      ? tokens["roomsPerRow"]
      : DEFAULT_ROOMS_PER_ROW;
  const rawAgentsPerRow =
    typeof tokens["agentsPerRow"] === "number"
      ? tokens["agentsPerRow"]
      : DEFAULT_AGENTS_PER_ROW;
  const rawPlotsPerRow =
    typeof tokens["plotsPerRow"] === "number"
      ? tokens["plotsPerRow"]
      : DEFAULT_PLOTS_PER_ROW;
  return {
    // Clamp baseCell to > 0 to prevent NaN/Infinity in coordinate arithmetic
    baseCell: Math.max(1, rawBaseCell),
    // Clamp *PerRow to >= 1 to prevent division-by-zero in slotToGrid
    drawerPageSize: Math.max(1, rawDrawerPageSize),
    roomsPerRow: Math.max(1, rawRoomsPerRow),
    agentsPerRow: Math.max(1, rawAgentsPerRow),
    plotsPerRow: Math.max(1, rawPlotsPerRow),
  };
}

/** Compute iso grid col/row from a linear slot and a row width. */
function slotToGrid(
  slot: number,
  perRow: number,
): { col: number; row: number } {
  return { col: slot % perRow, row: Math.floor(slot / perRow) };
}

// ---------------------------------------------------------------------------
// Ledger population helpers
// All collect ids in slot order (sorted by .slot ascending) so that the
// ledger's first-sight assignment matches the model's existing slot values.
// This guarantees that when the model carries pre-assigned slots (as it always
// does after Phase 1), the ledger reflects them faithfully.
// ---------------------------------------------------------------------------

/** Sort items by their `.slot` field ascending — pure, returns new array. */
function bySlot<T extends { slot: number }>(items: readonly T[]): T[] {
  return [...items].sort((a, b) => a.slot - b.slot);
}

/** Collect all node ids that need ledger entries, in slot order. */
function collectIds(model: EstateModel): string[] {
  const ids: string[] = [];

  for (const building of model.buildings) {
    ids.push(building.id);

    if (building.kind === "palace") {
      for (const wing of bySlot(building.wings)) {
        ids.push(wing.id);
        for (const room of bySlot(wing.rooms)) {
          ids.push(room.id);
          // Drawers within a room — include in ledger if present
          if (room.drawers != null) {
            for (const drawer of bySlot(room.drawers)) {
              ids.push(drawer.id);
            }
          }
        }
      }
    } else if (building.kind === "workshop") {
      // Emit synthetic `family:<name>` ids before each family's agents so the
      // family id is always assigned the slot immediately before its first agent.
      // This makes familyAgentBaseSlot = slotOf(mergedLedger, famId) + 1, a
      // stable quantity that survives agent deletion (ADR-0005).
      const seenFamilies = new Set<string>();
      for (const agent of bySlot(building.agents)) {
        if (!seenFamilies.has(agent.family)) {
          ids.push(`family:${agent.family}`);
          seenFamilies.add(agent.family);
        }
        ids.push(agent.id);
      }
    }
  }

  for (const plot of bySlot(model.grounds.plots)) {
    ids.push(plot.id);
  }

  return ids;
}

// ---------------------------------------------------------------------------
// Palace layout
// ---------------------------------------------------------------------------

function layoutPalace(
  palace: NookBuilding,
  ledger: Ledger,
  tk: ReturnType<typeof resolveTokens>,
  placed: Record<string, PlacedNode>,
  _ruinMap: Map<number, RuinNode>,
): void {
  const baseX = PALACE_ORIGIN_X;
  const baseY = 0;
  const c = tk.baseCell;

  // Palace building footprint — sized by wing count (min 2×2 cells)
  const wingCount = palace.wings.length;
  const palaceW = Math.max(2, wingCount) * 4 * c;
  const palaceH = 4 * c; // fixed depth for the outer wall

  const palaceSlot = slotOf(ledger, palace.id) ?? 0;
  placed[palace.id] = {
    slot: palaceSlot,
    isoX: baseX,
    isoY: baseY,
    w: palaceW,
    h: palaceH,
    kind: "palace",
    label: palace.title,
  };

  // Get the set of active wing ids to detect ruins
  const activeWingIds = new Set(palace.wings.map((w) => w.id));

  // Compute all wing ids ever seen in the ledger that belong to the palace
  // domain. We identify them by checking which ledger slots were assigned
  // during the palace id collection phase (between the palace id and the
  // workshop id in collectIds). Since we build a fresh ledger from current
  // model ids, ruin detection is done via ruinSlots() at the top level.
  // Here we lay out only active wings; ruins are handled at SceneGraph level.

  for (const wing of bySlot(palace.wings)) {
    layoutWing(wing, ledger, tk, placed, baseX, baseY + palaceH);
  }
}

function layoutWing(
  wing: Wing,
  ledger: Ledger,
  tk: ReturnType<typeof resolveTokens>,
  placed: Record<string, PlacedNode>,
  palaceBaseX: number,
  palaceBaseY: number,
): void {
  const c = tk.baseCell;
  // slotOf is always defined here: collectIds + buildLedger assign every active id.
  // The `?? 0` is an unreachable safety guard (not a model-slot read).
  const wingSlot = slotOf(ledger, wing.id) ?? 0;

  // Wing position: laid out by slot along X axis
  const wingX = palaceBaseX + wingSlot * 6 * c;
  const wingY = palaceBaseY;

  // Wing footprint: 5 wide × room rows tall (scaled by room count)
  const roomCount = wing.rooms.length;
  const rowCount = Math.ceil(Math.max(1, roomCount) / tk.roomsPerRow);
  const wingW = 5 * c;
  const wingH = (rowCount * 3 + 1) * c;

  placed[wing.id] = {
    slot: wingSlot,
    isoX: wingX,
    isoY: wingY,
    w: wingW,
    h: wingH,
    kind: "wing",
    label: wing.title,
    wingType: wing.type,
    roomCount: wing.rooms.length,
    drawerCount: wing.drawer_total,
  };

  for (const room of bySlot(wing.rooms)) {
    layoutRoom(room, ledger, tk, placed, wingX, wingY, wing.id);
  }
}

function layoutRoom(
  room: Room,
  ledger: Ledger,
  tk: ReturnType<typeof resolveTokens>,
  placed: Record<string, PlacedNode>,
  wingBaseX: number,
  wingBaseY: number,
  parentId: string,
): void {
  const c = tk.baseCell;
  // slotOf is always defined here: collectIds + buildLedger assign every active id.
  const roomSlot = slotOf(ledger, room.id) ?? 0;

  const { col, row } = slotToGrid(roomSlot, tk.roomsPerRow);
  const roomX = wingBaseX + col * 2 * c;
  const roomY = wingBaseY + c + row * 3 * c; // +c for wing header

  const roomW = 2 * c;
  const roomH = 2 * c;

  // Drawer glyph bucket: floor(drawer_count / page_size), clamped to [0, 5]
  const drawerBucket = Math.min(
    5,
    Math.floor(room.drawer_count / tk.drawerPageSize),
  );

  placed[room.id] = {
    slot: roomSlot,
    isoX: roomX,
    isoY: roomY,
    w: roomW,
    h: roomH,
    kind: "room",
    label: room.title,
    parentId,
    drawerBucket,
  };
}

// ---------------------------------------------------------------------------
// Workshop layout
// ---------------------------------------------------------------------------

function layoutWorkshop(
  workshop: WorkshopBuilding,
  ledger: Ledger,
  tk: ReturnType<typeof resolveTokens>,
  placed: Record<string, PlacedNode>,
): void {
  const baseX = WORKSHOP_ORIGIN_X;
  const baseY = 0;
  const c = tk.baseCell;

  // Group agents by family — preserving within-family slot order.
  // We use a Map (insertion-ordered) keyed by family, collecting agents in
  // their bySlot order. The family itself gets a bucket slot by first-sight
  // order of the family key.
  const familyOrder: string[] = [];
  const familyAgents: Map<string, Agent[]> = new Map();
  for (const agent of bySlot(workshop.agents)) {
    const fam = agent.family;
    if (!familyAgents.has(fam)) {
      familyOrder.push(fam);
      familyAgents.set(fam, []);
    }
    familyAgents.get(fam)!.push(agent);
  }

  const agentCount = workshop.agents.length;
  const workshopW = Math.max(4, Math.ceil(agentCount / tk.agentsPerRow)) * 3 * c;
  const familyCount = familyOrder.length;
  const workshopH = Math.max(3, familyCount) * 5 * c;

  const workshopSlot = slotOf(ledger, workshop.id) ?? 0;
  placed[workshop.id] = {
    slot: workshopSlot,
    isoX: baseX,
    isoY: baseY,
    w: workshopW,
    h: workshopH,
    kind: "workshop",
    label: workshop.title,
  };

  // Family wings — bucketed, slot by first-sight family order
  for (let famIdx = 0; famIdx < familyOrder.length; famIdx++) {
    const fam = familyOrder[famIdx]!;
    const agents = familyAgents.get(fam)!;

    const famX = baseX;
    const famY = baseY + famIdx * 5 * c;
    const famW = workshopW;
    const famH = 4 * c;
    const famId = `family:${fam}`;

    // family-wing synthetic node — slot comes from the merged ledger because
    // collectIds emits family:<name> before each family's agents.
    const famNodeSlot = slotOf(ledger, famId) ?? famIdx;
    placed[famId] = {
      slot: famNodeSlot,
      isoX: famX,
      isoY: famY,
      w: famW,
      h: famH,
      kind: "family-wing",
      label: fam,
      parentId: workshop.id,
    };

    // Agent base slot = famNodeSlot + 1.
    // collectIds guarantees family:<name> is assigned the slot immediately
    // before the family's first agent, so this offset is stable across
    // agent deletions (ADR-0005): survivors' within-family offsets are unchanged.
    const familyAgentBaseSlot = famNodeSlot + 1;

    // Agent rooms within the family wing
    for (const agent of agents) {
      // slotOf is always defined here: collectIds + buildLedger assign every active id.
      const agentSlot = slotOf(ledger, agent.id) ?? 0;

      // Within-family position = stable offset from the family agent base slot.
      // Removing a middle agent leaves a coordinate gap; survivors are unchanged.
      const withinFamilySlot = agentSlot - familyAgentBaseSlot;
      const { col, row } = slotToGrid(withinFamilySlot, tk.agentsPerRow);
      const agentX = famX + col * 2 * c;
      const agentY = famY + c + row * 2 * c;

      placed[agent.id] = {
        slot: agentSlot,
        isoX: agentX,
        isoY: agentY,
        w: 2 * c,
        h: 1 * c,
        kind: "agent-room",
        label: agent.id,
        parentId: famId,
      };
    }
  }
}

// ---------------------------------------------------------------------------
// Grounds layout
// ---------------------------------------------------------------------------

function layoutGrounds(
  plots: readonly Plot[],
  ledger: Ledger,
  tk: ReturnType<typeof resolveTokens>,
  placed: Record<string, PlacedNode>,
): void {
  const baseX = GROUNDS_ORIGIN_X;
  const baseY = 0;
  const c = tk.baseCell;

  const plotCount = plots.length;
  const cols = Math.min(plotCount, tk.plotsPerRow);
  const rows = Math.ceil(Math.max(1, plotCount) / tk.plotsPerRow);
  const groundsW = Math.max(2, cols) * 4 * c;
  const groundsH = Math.max(2, rows) * 4 * c;

  placed["grounds"] = {
    slot: 0,
    isoX: baseX,
    isoY: baseY,
    w: groundsW,
    h: groundsH,
    kind: "grounds",
    label: "Grounds",
  };

  for (const plot of bySlot(plots)) {
    // slotOf is always defined here: collectIds + buildLedger assign every active id.
    const plotSlot = slotOf(ledger, plot.id) ?? 0;
    const { col, row } = slotToGrid(plotSlot, tk.plotsPerRow);
    const plotX = baseX + col * 4 * c;
    const plotY = baseY + row * 4 * c;

    const plotNode: PlacedNode = {
      slot: plotSlot,
      isoX: plotX,
      isoY: plotY,
      w: 3 * c,
      h: 3 * c,
      kind: "plot",
      label: plot.title,
    };
    // exactOptionalPropertyTypes: only set dirty when it is a real boolean
    if (plot.dirty === true || plot.dirty === false) {
      plotNode.dirty = plot.dirty;
    }
    placed[plot.id] = plotNode;
  }
}

// ---------------------------------------------------------------------------
// Ruin synthesis
// ---------------------------------------------------------------------------

/**
 * For each ruin slot, synthesise a RuinNode from the slot number.
 * We reconstruct position from the ledger slot number using a fixed rule:
 * ruins are placed in a dedicated ruin row below the palace, at their slot's
 * X position, so spatial memory is preserved as "decayed".
 */
function buildRuinNodes(
  ruinSlotList: readonly number[],
  ledger: Ledger,
  placed: Record<string, PlacedNode>,
  deletedIdBySlot: Map<number, string>,
): RuinNode[] {
  void ledger; // ledger carried for future Phase 3 label recovery
  const ruins: RuinNode[] = [];
  const c = DEFAULT_BASE_CELL;

  // Ruins row: fixed Y below palace zone, X by slot
  const RUIN_BASE_Y = 40 * c;

  for (const slot of ruinSlotList) {
    const deletedId = deletedIdBySlot.get(slot) ?? `deleted:slot:${slot}`;

    // If the node was active in a prior run, placed may have had it; now it
    // doesn't. We reconstruct a plausible position from slot.
    // Use the same slot-to-grid logic with a wide row to spread ruins out.
    const { col, row } = slotToGrid(slot, DEFAULT_PLOTS_PER_ROW * 2);
    const ruinX = PALACE_ORIGIN_X + col * 4 * c;
    const ruinY = RUIN_BASE_Y + row * 4 * c;

    ruins.push({
      slot,
      isoX: ruinX,
      isoY: ruinY,
      w: 2 * c,
      h: 2 * c,
      kind: "room", // default to room ruin (most common case)
      deletedId,
    });
  }

  void placed; // carried for future use (e.g. last-known-coords recovery)
  return ruins;
}

// ---------------------------------------------------------------------------
// Main layout function
// ---------------------------------------------------------------------------

/**
 * layout — pure, deterministic. Same model + tokens ⇒ byte-identical output.
 *
 * Steps:
 * 1. Collect all node ids from the current model in slot order.
 * 2. Build a fresh ledger from those ids; merge with priorLedger (prior wins).
 *    The merged ledger is the stable id→slot map for this render pass.
 * 3. Lay out palace, workshop, grounds — placement driven by merged ledger slots.
 *    Within-family agent position derives from (agentSlot - familyBaseSlot) so
 *    removing a middle sibling leaves a gap rather than reflowing survivors.
 * 4. Compute ruin slots (ids in priorLedger absent from current model).
 * 5. Synthesise ruin nodes.
 * 6. Return the scene graph.
 *
 * ADR-0005 invariant: a node id's slot is permanent after first-sight.
 * Prior-ledger slots always win; new ids are appended at the prior frontier.
 */
export function layout(
  model: EstateModel,
  tokens: DesignTokens,
  priorLedger: Ledger = EMPTY_LEDGER,
): SceneGraph {
  const tk = resolveTokens(tokens);

  // 1. Collect all ids from the current model in slot order
  const currentIds = collectIds(model);

  // 2. Build ledger: merge prior (prior wins) with fresh to get stable slots.
  //    Prior-ledger slots are authoritative; new ids are appended at the
  //    prior frontier. This is the ADR-0005 invariant: never repack slots.
  const freshLedger = buildLedger(currentIds);
  const ledger = mergeLedgers(priorLedger, freshLedger);

  // 3. Compute ruin slots: ids in the PRIOR ledger but not in the current model
  const activeIdSet = new Set(currentIds);
  const ruinSlotList = ruinSlots(priorLedger, activeIdSet);

  // Build a reverse map: slot → deleted id (from prior ledger)
  const deletedIdBySlot = new Map<number, string>();
  for (const [id, slot] of Object.entries(priorLedger.slots)) {
    if (!activeIdSet.has(id)) {
      deletedIdBySlot.set(slot, id);
    }
  }

  // 4. Place all active nodes
  const placed: Record<string, PlacedNode> = {};
  const ruinMap = new Map<number, RuinNode>();

  for (const building of model.buildings) {
    if (building.kind === "palace") {
      layoutPalace(building, ledger, tk, placed, ruinMap);
    } else if (building.kind === "workshop") {
      layoutWorkshop(building, ledger, tk, placed);
    }
  }

  layoutGrounds(model.grounds.plots, ledger, tk, placed);

  // 5. Synthesise ruin nodes
  const ruins = buildRuinNodes(ruinSlotList, ledger, placed, deletedIdBySlot);

  // 6. Assemble scene graph
  const totalSlots = ledger.nextSlot;

  return {
    placed,
    ruins,
    meta: {
      revision: model.revision,
      capturedAt: model.captured_at,
      totalSlots,
      ruinCount: ruins.length,
    },
  };
}
