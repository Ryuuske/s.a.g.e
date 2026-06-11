/**
 * Layout determinism + append-stability tests (Phase 2a, item 2a.3).
 *
 * Load-bearing invariants under test:
 * 1. Same model ⇒ byte-identical scene graph (JSON.stringify equality across
 *    two independent calls, including a structuredClone of the input).
 * 2. Append-stability: removing a middle wing/agent id leaves every surviving
 *    id at its exact slot/coords, and the removed id's slot appears as a ruin.
 * 3. Determinism across fresh ledger builds (no hidden global state).
 * 4. Bucketing is slot-stable (drawer buckets are deterministic).
 *
 * Fixture: tests/estate/fixtures/estate-model.sample.json (single-source).
 *
 * WHERE: src/sage_mcp/estate/web/test/layout.determinism.test.ts
 */

import { describe, expect, it } from "vitest";
import type {
  Agent,
  EstateModel,
  NookBuilding,
  PlacedNode,
  WorkshopBuilding,
} from "../src/model/types";
import { layout } from "../src/layout/layout";
import { buildLedger, EMPTY_LEDGER } from "../src/layout/ledger";
import type { Ledger } from "../src/layout/ledger";

// ---------------------------------------------------------------------------
// Fixture import — single-source; do NOT copy-paste fixture contents here.
// The JSON is imported directly; TypeScript will validate the shape.
// ---------------------------------------------------------------------------
import sampleRaw from "../../../../../tests/estate/fixtures/estate-model.sample.json" assert {
  type: "json",
};
const sample = sampleRaw as EstateModel;

// ---------------------------------------------------------------------------
// Shared tokens (minimal — let layout engine use defaults for unset keys)
// ---------------------------------------------------------------------------
const TOKENS = {};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Remove the wing at `wingId` from the nook palace. Returns a deep clone. */
function removeWingById(model: EstateModel, wingId: string): EstateModel {
  const cloned = structuredClone(model) as EstateModel;
  for (const b of cloned.buildings) {
    if (b.kind === "palace") {
      b.wings = b.wings.filter((w) => w.id !== wingId);
    }
  }
  return cloned;
}

/** Remove the agent at `agentId` from the workshop. Returns a deep clone. */
function removeAgentById(model: EstateModel, agentId: string): EstateModel {
  const cloned = structuredClone(model) as EstateModel;
  for (const b of cloned.buildings) {
    if (b.kind === "workshop") {
      b.agents = b.agents.filter((a) => a.id !== agentId);
    }
  }
  return cloned;
}

/** Remove the plot at `plotId` from the grounds. Returns a deep clone. */
function removePlotById(model: EstateModel, plotId: string): EstateModel {
  const cloned = structuredClone(model) as EstateModel;
  cloned.grounds.plots = cloned.grounds.plots.filter((p) => p.id !== plotId);
  return cloned;
}

/**
 * Build a prior ledger from a full model, mirroring collectIds order exactly:
 * buildings → (palace: wings → rooms → drawers) | (workshop: family:<name> + agents) → plots.
 *
 * Must stay in sync with layout.ts collectIds. Family synthetic ids are emitted
 * before the first agent of each family, as collectIds does.
 */
function priorLedgerFrom(model: EstateModel): Ledger {
  const ids: string[] = [];
  for (const b of model.buildings) {
    ids.push(b.id);
    if (b.kind === "palace") {
      const wings = [...b.wings].sort((a, b2) => a.slot - b2.slot);
      for (const w of wings) {
        ids.push(w.id);
        const rooms = [...w.rooms].sort((a, b2) => a.slot - b2.slot);
        for (const r of rooms) {
          ids.push(r.id);
          if (r.drawers != null) {
            const drawers = [...r.drawers].sort((a, b2) => a.slot - b2.slot);
            for (const d of drawers) ids.push(d.id);
          }
        }
      }
    } else if (b.kind === "workshop") {
      const agents = [...b.agents].sort((a, b2) => a.slot - b2.slot);
      const seenFamilies = new Set<string>();
      for (const a of agents) {
        if (!seenFamilies.has(a.family)) {
          ids.push(`family:${a.family}`);
          seenFamilies.add(a.family);
        }
        ids.push(a.id);
      }
    }
  }
  const plots = [...model.grounds.plots].sort((a, b2) => a.slot - b2.slot);
  for (const p of plots) ids.push(p.id);
  return buildLedger(ids);
}

// ---------------------------------------------------------------------------
// Minimal inline model builders for isolated tests
// ---------------------------------------------------------------------------

/** Minimal EstateModel with no buildings and no plots. */
function emptyModel(): EstateModel {
  return {
    version: "1.0",
    revision: 1,
    captured_at: "2026-01-01T00:00:00Z",
    property: { name: "test", isolation: {}, health: { governance: {}, store: {} } },
    buildings: [],
    grounds: { plots: [] },
    outbuildings: { horrea: {}, tablinum: {}, gate: {} },
  };
}

/**
 * Minimal workshop-only EstateModel.
 * families: dev(A0 slot=0, A1 slot=1), sec(B0 slot=2)
 */
function multiFamilyModel(): EstateModel {
  const base = emptyModel();
  const workshop: WorkshopBuilding = {
    id: "workshop",
    kind: "workshop",
    title: "Workshop",
    agents: [
      { id: "A0", family: "dev", slot: 0 },
      { id: "A1", family: "dev", slot: 1 },
      { id: "B0", family: "sec", slot: 2 },
    ] as Agent[],
    armory: {},
  };
  base.buildings = [workshop];
  return base;
}

// ---------------------------------------------------------------------------
// Test suite
// ---------------------------------------------------------------------------

describe("layout determinism", () => {
  it("same model => byte-identical scene graph (two independent calls)", () => {
    const a = JSON.stringify(layout(sample, TOKENS));
    const b = JSON.stringify(layout(structuredClone(sample) as EstateModel, TOKENS));
    expect(a).toBe(b);
  });

  it("same model, third independent call => still byte-identical", () => {
    const a = JSON.stringify(layout(sample, TOKENS));
    const c = JSON.stringify(layout(structuredClone(sample) as EstateModel, TOKENS));
    expect(a).toBe(c);
  });

  it("prior-ledger path and no-ledger path agree on placed ids and slots", () => {
    // Build a prior that contains exactly the same ids as the model (no ruins).
    const prior = priorLedgerFrom(sample);
    const withPrior = layout(sample, TOKENS, prior);
    const withoutPrior = layout(sample, TOKENS);

    // Placed id sets must be identical.
    const keysWithPrior = Object.keys(withPrior.placed).sort();
    const keysWithout = Object.keys(withoutPrior.placed).sort();
    expect(keysWithPrior).toEqual(keysWithout);

    // Each placed id must carry the same slot in both paths.
    for (const id of keysWithPrior) {
      expect(withPrior.placed[id]!.slot).toBe(withoutPrior.placed[id]!.slot);
    }

    // meta.totalSlots may legitimately differ (prior includes family: ids whose
    // nextSlot frontier differs from the no-prior build) — that is acceptable.
    // Only ruins.length is pinned: no deleted ids → 0 ruins in both cases.
    expect(withPrior.ruins).toHaveLength(0);
    expect(withoutPrior.ruins).toHaveLength(0);
  });
});

describe("append-stability: removing a middle wing", () => {
  // The middle wing is "wing:personal:Personal" (slot 1 out of 0,1,2).
  const MIDDLE_WING_ID = "wing:personal:Personal";

  it("surviving wings keep their exact slot and coords after middle deletion", () => {
    // Build a prior ledger from the full model so the layout engine knows
    // about the deleted id and can surface it as a ruin.
    const prior = priorLedgerFrom(sample);

    const before = layout(sample, TOKENS, prior);
    const pruned = removeWingById(sample, MIDDLE_WING_ID);
    const after = layout(pruned, TOKENS, prior);

    // Collect surviving wing ids
    const palace = sample.buildings.find((b) => b.kind === "palace") as NookBuilding;
    const survivingWingIds = palace.wings
      .filter((w) => w.id !== MIDDLE_WING_ID)
      .map((w) => w.id);

    for (const wingId of survivingWingIds) {
      const nodeBefore = before.placed[wingId] as PlacedNode;
      const nodeAfter = after.placed[wingId] as PlacedNode;
      expect(nodeAfter).toBeDefined();
      // Slot must be identical
      expect(nodeAfter.slot).toBe(nodeBefore.slot);
      // Coordinates must be identical — no reflow
      expect(nodeAfter.isoX).toBe(nodeBefore.isoX);
      expect(nodeAfter.isoY).toBe(nodeBefore.isoY);
      expect(nodeAfter.w).toBe(nodeBefore.w);
      expect(nodeAfter.h).toBe(nodeBefore.h);
    }
  });

  it("deleted middle wing's slot appears as a ruin, not repacked", () => {
    const prior = priorLedgerFrom(sample);

    const before = layout(sample, TOKENS, prior);
    const deletedSlot = (before.placed[MIDDLE_WING_ID] as PlacedNode).slot;

    const pruned = removeWingById(sample, MIDDLE_WING_ID);
    const after = layout(pruned, TOKENS, prior);

    // The deleted id must NOT be in placed
    expect(after.placed[MIDDLE_WING_ID]).toBeUndefined();

    // The slot must appear in ruins
    const ruinSlotValues = after.ruins.map((r) => r.slot);
    expect(ruinSlotValues).toContain(deletedSlot);

    // The ruin entry for the deleted id must reference the correct deletedId
    const ruinEntry = after.ruins.find((r) => r.deletedId === MIDDLE_WING_ID);
    expect(ruinEntry).toBeDefined();
    expect(ruinEntry!.slot).toBe(deletedSlot);
  });

  it("rooms inside surviving wings keep their exact coords after deletion", () => {
    const prior = priorLedgerFrom(sample);
    const before = layout(sample, TOKENS, prior);
    const pruned = removeWingById(sample, MIDDLE_WING_ID);
    const after = layout(pruned, TOKENS, prior);

    // Check rooms of the first wing (slot 0, "wing:dev:sage")
    const palace = sample.buildings.find((b) => b.kind === "palace") as NookBuilding;
    const firstWing = palace.wings.find((w) => w.slot === 0)!;
    for (const room of firstWing.rooms) {
      const roomBefore = before.placed[room.id] as PlacedNode;
      const roomAfter = after.placed[room.id] as PlacedNode;
      expect(roomAfter).toBeDefined();
      expect(roomAfter.slot).toBe(roomBefore.slot);
      expect(roomAfter.isoX).toBe(roomBefore.isoX);
      expect(roomAfter.isoY).toBe(roomBefore.isoY);
    }
  });
});

describe("append-stability: removing a middle agent (single-family, sample fixture)", () => {
  // The middle agent is "dev-code-implementer" (slot 1 of slot 0,1) — both in family "dev".
  const MIDDLE_AGENT_ID = "dev-code-implementer";

  it("surviving agents keep their exact slot and coords after middle deletion", () => {
    const prior = priorLedgerFrom(sample);
    const before = layout(sample, TOKENS, prior);
    const pruned = removeAgentById(sample, MIDDLE_AGENT_ID);
    const after = layout(pruned, TOKENS, prior);

    const ws = sample.buildings.find((b) => b.kind === "workshop") as WorkshopBuilding;
    const survivingAgents = ws.agents.filter((a) => a.id !== MIDDLE_AGENT_ID);

    for (const agent of survivingAgents) {
      const nodeBefore = before.placed[agent.id] as PlacedNode;
      const nodeAfter = after.placed[agent.id] as PlacedNode;
      expect(nodeAfter).toBeDefined();
      expect(nodeAfter.slot).toBe(nodeBefore.slot);
      expect(nodeAfter.isoX).toBe(nodeBefore.isoX);
      expect(nodeAfter.isoY).toBe(nodeBefore.isoY);
    }
  });

  it("deleted middle agent's slot appears as a ruin", () => {
    const prior = priorLedgerFrom(sample);
    const before = layout(sample, TOKENS, prior);
    const deletedSlot = (before.placed[MIDDLE_AGENT_ID] as PlacedNode).slot;

    const pruned = removeAgentById(sample, MIDDLE_AGENT_ID);
    const after = layout(pruned, TOKENS, prior);

    expect(after.placed[MIDDLE_AGENT_ID]).toBeUndefined();

    const ruinEntry = after.ruins.find((r) => r.deletedId === MIDDLE_AGENT_ID);
    expect(ruinEntry).toBeDefined();
    expect(ruinEntry!.slot).toBe(deletedSlot);
  });
});

describe("append-stability: multi-family agent deletion (ADR-0005 regression)", () => {
  // Fixture: families dev(A0 slot=0, A1 slot=1) + sec(B0 slot=2).
  // Remove A0 (NON-last in dev). A1 and B0 must be UNCHANGED.
  //
  // This test would have FAILED on the pre-fix code (which used agentIdx for
  // within-family positioning), producing a reflow of A1 from col=1 to col=0.
  // After the fix (slot-relative positioning via familyAgentBaseSlot), A1 stays
  // at its original coords and A0's slot surfaces as a ruin.

  it("removing non-last agent in a family does not reflow siblings or other families", () => {
    const model = multiFamilyModel();
    const prior = priorLedgerFrom(model);

    const before = layout(model, TOKENS, prior);
    const pruned = removeAgentById(model, "A0");
    const after = layout(pruned, TOKENS, prior);

    // A1 was at col=1 in dev family — must remain there (no reflow).
    const a1Before = before.placed["A1"] as PlacedNode;
    const a1After = after.placed["A1"] as PlacedNode;
    expect(a1After).toBeDefined();
    expect(a1After.slot).toBe(a1Before.slot);
    expect(a1After.isoX).toBe(a1Before.isoX); // must not shift left to col=0
    expect(a1After.isoY).toBe(a1Before.isoY);

    // A1 isoX must equal before value (col=1 → X = WORKSHOP_ORIGIN_X + 1*2*c = 32+2 = 34)
    expect(a1After.isoX).toBe(34);
    expect(a1After.isoY).toBe(1); // famY=0, +c=1

    // B0 (other family) must be completely unchanged.
    const b0Before = before.placed["B0"] as PlacedNode;
    const b0After = after.placed["B0"] as PlacedNode;
    expect(b0After).toBeDefined();
    expect(b0After.slot).toBe(b0Before.slot);
    expect(b0After.isoX).toBe(b0Before.isoX);
    expect(b0After.isoY).toBe(b0Before.isoY);

    // A0's slot must surface as a ruin.
    const a0Slot = before.placed["A0"]!.slot;
    const ruinEntry = after.ruins.find((r) => r.deletedId === "A0");
    expect(ruinEntry).toBeDefined();
    expect(ruinEntry!.slot).toBe(a0Slot);

    // A0 must not appear in placed.
    expect(after.placed["A0"]).toBeUndefined();
  });

  it("surviving agent A1 has exact pinned coords (isoX=34, isoY=1)", () => {
    // Pin the exact coordinates so any future regression is caught immediately.
    // Derivation: WORKSHOP_ORIGIN_X=32, c=1, agentsPerRow=4
    //   family:dev famNodeSlot=1, familyAgentBaseSlot=2
    //   A1 agentSlot=3, withinFamilySlot=1 → col=1 → isoX=32+2=34, isoY=0+1=1
    const model = multiFamilyModel();
    const prior = priorLedgerFrom(model);
    const pruned = removeAgentById(model, "A0");
    const after = layout(pruned, TOKENS, prior);

    const a1 = after.placed["A1"] as PlacedNode;
    expect(a1.isoX).toBe(34);
    expect(a1.isoY).toBe(1);
  });

  it("B0 (sec family) has pinned coords unchanged by A0 removal (isoX=32, isoY=6)", () => {
    // Derivation: sec family at famIdx=1, famY=0+1*5=5, c=1
    //   family:sec famNodeSlot=4, familyAgentBaseSlot=5
    //   B0 agentSlot=5, withinFamilySlot=0 → col=0 → isoX=32+0=32, isoY=5+1=6
    const model = multiFamilyModel();
    const prior = priorLedgerFrom(model);
    const before = layout(model, TOKENS, prior);
    const pruned = removeAgentById(model, "A0");
    const after = layout(pruned, TOKENS, prior);

    const b0Before = before.placed["B0"] as PlacedNode;
    const b0After = after.placed["B0"] as PlacedNode;
    expect(b0Before.isoX).toBe(32);
    expect(b0Before.isoY).toBe(6);
    expect(b0After.isoX).toBe(32);
    expect(b0After.isoY).toBe(6);
  });
});

describe("bucketing determinism", () => {
  it("drawer bucket is deterministic for rooms with known drawer_count", () => {
    const a = layout(sample, TOKENS);
    const b = layout(structuredClone(sample) as EstateModel, TOKENS);

    // Check rooms with non-zero drawer counts
    const palace = sample.buildings.find((b2) => b2.kind === "palace") as NookBuilding;
    for (const wing of palace.wings) {
      for (const room of wing.rooms) {
        if (room.drawer_count > 0) {
          const nodeA = a.placed[room.id] as PlacedNode;
          const nodeB = b.placed[room.id] as PlacedNode;
          expect(nodeA.drawerBucket).toBe(nodeB.drawerBucket);
        }
      }
    }
  });

  it("drawer bucket uses floor(count / pageSize) with default pageSize=10", () => {
    // Room "room:dev:sage:main" has drawer_count: 5 → bucket = floor(5/10) = 0
    const graph = layout(sample, TOKENS);
    const room = graph.placed["room:dev:sage:main"] as PlacedNode;
    expect(room).toBeDefined();
    expect(room.drawerBucket).toBe(0); // floor(5 / 10) = 0

    // Room with drawer_count: 4 → bucket = floor(4/10) = 0
    const roomPersonal = graph.placed["room:personal:Personal:core"] as PlacedNode;
    expect(roomPersonal).toBeDefined();
    expect(roomPersonal.drawerBucket).toBe(0); // floor(4 / 10) = 0
  });

  it("custom drawerPageSize token changes bucketing deterministically", () => {
    const customTokens = { drawerPageSize: 3 };
    const a = JSON.stringify(layout(sample, customTokens));
    const b = JSON.stringify(layout(structuredClone(sample) as EstateModel, customTokens));
    expect(a).toBe(b);

    // With pageSize=3: room with drawer_count=5 → floor(5/3)=1
    const graphA = layout(sample, customTokens);
    const room = graphA.placed["room:dev:sage:main"] as PlacedNode;
    expect(room.drawerBucket).toBe(1);
  });

  it("drawer_count 9 → bucket 0 (default pageSize=10)", () => {
    // floor(9/10) = 0
    const graph = layout(sample, TOKENS);
    // Use the room with drawer_count=5 as a proxy (already tested) — add an
    // inline-model test for bucket boundary values.
    // Inline: build a model with a single room at drawer_count=9.
    const m = emptyModel();
    const palace: NookBuilding = {
      id: "nook",
      kind: "palace",
      title: "N",
      wings: [
        {
          id: "wing:dev:test",
          type: "dev",
          title: "test",
          slot: 0,
          rooms: [{ id: "room:a", title: "a", slot: 0, drawer_count: 9 }],
          hall_counts: {},
          drawer_total: 9,
        },
      ],
      tunnels: [],
      closets: {},
      kg: {},
    };
    m.buildings = [palace];
    const g = layout(m, TOKENS);
    expect((g.placed["room:a"] as PlacedNode).drawerBucket).toBe(0); // floor(9/10)=0
    void graph; // suppress unused warning
  });

  it("drawer_count 10 → bucket 1, 50 → bucket 5, 60 → bucket 5 (clamped)", () => {
    const makeRoom = (id: string, count: number) => ({
      id,
      title: id,
      slot: 0,
      drawer_count: count,
    });
    const makePalace = (id: string, count: number): NookBuilding => ({
      id: "nook",
      kind: "palace" as const,
      title: "N",
      wings: [
        {
          id: "wing:dev:test",
          type: "dev" as const,
          title: "test",
          slot: 0,
          rooms: [makeRoom(id, count)],
          hall_counts: {},
          drawer_total: count,
        },
      ],
      tunnels: [],
      closets: {},
      kg: {},
    });

    for (const [count, expectedBucket] of [
      [10, 1],  // floor(10/10)=1
      [50, 5],  // floor(50/10)=5
      [60, 5],  // floor(60/10)=6 → clamped to 5
    ] as [number, number][]) {
      const m = emptyModel();
      m.buildings = [makePalace(`room:${count}`, count)];
      const g = layout(m, TOKENS);
      expect(
        (g.placed[`room:${count}`] as PlacedNode).drawerBucket,
        `drawer_count=${count} should give bucket=${expectedBucket}`,
      ).toBe(expectedBucket);
    }
  });
});

describe("prior-ledger priority", () => {
  it("priorLedger assigns slot 99 to an id → layout places it at slot 99", () => {
    // Build a model with a single plot "plot:x" at slot=0.
    const m = emptyModel();
    m.grounds.plots = [{ id: "plot:x", title: "x", slot: 0 }];

    // Prior ledger assigns slot 99 to "plot:x" (ignoring the model's slot=0).
    const prior = buildLedger(["plot:x"]);
    // Manually override: rebuild with nextSlot placed at 99
    const priorAt99: Ledger = { slots: { "plot:x": 99 }, nextSlot: 100 };

    const g = layout(m, TOKENS, priorAt99);
    const plotNode = g.placed["plot:x"] as PlacedNode;
    expect(plotNode).toBeDefined();
    expect(plotNode.slot).toBe(99);
    void prior; // used above to show intent
  });
});

describe("edge cases", () => {
  it("empty buildings array produces a scene graph with only grounds container", () => {
    const m = emptyModel();
    const g = layout(m, TOKENS);
    // Only "grounds" should be in placed (no palace, no workshop)
    expect(g.placed["grounds"]).toBeDefined();
    expect(Object.keys(g.placed)).toEqual(["grounds"]);
    expect(g.ruins).toHaveLength(0);
  });

  it("zero-plot grounds still emits the grounds container node", () => {
    const m = emptyModel();
    const g = layout(m, TOKENS);
    const grounds = g.placed["grounds"] as PlacedNode;
    expect(grounds).toBeDefined();
    expect(grounds.kind).toBe("grounds");
    // Width is based on max(2, cols)*4*c with cols=min(0,4)=0 → max(2,0)=2 → 8
    expect(grounds.w).toBe(8);
  });

  it("all-ruins: every prior id absent from model surfaces as ruin, placed is minimal", () => {
    // Prior ledger has a wing id that is NOT in the current model.
    const prior: Ledger = { slots: { "wing:old:ghost": 0 }, nextSlot: 1 };
    const m = emptyModel(); // no buildings, no plots
    const g = layout(m, TOKENS, prior);

    expect(g.placed["wing:old:ghost"]).toBeUndefined();
    const ruin = g.ruins.find((r) => r.deletedId === "wing:old:ghost");
    expect(ruin).toBeDefined();
    expect(ruin!.slot).toBe(0);
  });

  it("middle grounds-plot deletion: other plots keep exact coords", () => {
    // Build a model with 3 plots at slots 0,1,2.
    const m = emptyModel();
    m.grounds.plots = [
      { id: "plot:a", title: "a", slot: 0 },
      { id: "plot:b", title: "b", slot: 1 },
      { id: "plot:c", title: "c", slot: 2 },
    ];
    const prior = priorLedgerFrom(m);
    const before = layout(m, TOKENS, prior);
    const pruned = removePlotById(m, "plot:b");
    const after = layout(pruned, TOKENS, prior);

    // plot:a and plot:c must be unchanged
    for (const id of ["plot:a", "plot:c"]) {
      const nb = before.placed[id] as PlacedNode;
      const na = after.placed[id] as PlacedNode;
      expect(na).toBeDefined();
      expect(na.slot).toBe(nb.slot);
      expect(na.isoX).toBe(nb.isoX);
      expect(na.isoY).toBe(nb.isoY);
    }

    // plot:b must be a ruin
    expect(after.placed["plot:b"]).toBeUndefined();
    const ruin = after.ruins.find((r) => r.deletedId === "plot:b");
    expect(ruin).toBeDefined();
  });

  it("zero-room wing has a non-zero footprint (min-1 room row is applied)", () => {
    // A wing with rooms=[] should still produce a placed node with h > 0.
    const m = emptyModel();
    const palace: NookBuilding = {
      id: "nook",
      kind: "palace",
      title: "N",
      wings: [
        {
          id: "wing:dev:empty",
          type: "dev",
          title: "empty",
          slot: 0,
          rooms: [],
          hall_counts: {},
          drawer_total: 0,
        },
      ],
      tunnels: [],
      closets: {},
      kg: {},
    };
    m.buildings = [palace];
    const g = layout(m, TOKENS);
    const wingNode = g.placed["wing:dev:empty"] as PlacedNode;
    expect(wingNode).toBeDefined();
    // h = (rowCount * 3 + 1) * c; rowCount = ceil(max(1,0)/4) = 1 → h = 4
    expect(wingNode.h).toBe(4);
    expect(wingNode.w).toBeGreaterThan(0);
  });

  it("family-wing node is placed for each family even without prior ledger", () => {
    // Without a prior ledger, family synthetic nodes must still be placed.
    const model = multiFamilyModel();
    const g = layout(model, TOKENS);
    expect(g.placed["family:dev"]).toBeDefined();
    expect(g.placed["family:sec"]).toBeDefined();
    expect(g.placed["family:dev"]!.kind).toBe("family-wing");
  });

  it("drawers are NOT emitted into placed (drawer ids go into ledger for stability, not scene graph)", () => {
    // Drawer nodes are tracked in the ledger for slot stability but the layout
    // engine does not emit them as PlacedNodes in the current phase.
    // This test pins that intent: no "drawer:" key should appear in placed.
    const g = layout(sample, TOKENS);
    const drawerKeys = Object.keys(g.placed).filter((k) => k.startsWith("drawer:"));
    expect(drawerKeys).toHaveLength(0);
  });
});

describe("resolveTokens zero/negative clamp", () => {
  it("baseCell=0 is clamped to 1 — no NaN/Infinity coords", () => {
    const g = layout(sample, { baseCell: 0 });
    // If clamp fails, isoX/isoY/w/h would be NaN/Infinity.
    for (const node of Object.values(g.placed)) {
      expect(isFinite(node.isoX)).toBe(true);
      expect(isFinite(node.isoY)).toBe(true);
      expect(isFinite(node.w)).toBe(true);
      expect(isFinite(node.h)).toBe(true);
    }
  });

  it("agentsPerRow=0 is clamped to 1 — no division-by-zero in slotToGrid", () => {
    const g = layout(sample, { agentsPerRow: 0 });
    for (const node of Object.values(g.placed)) {
      expect(isFinite(node.isoX)).toBe(true);
      expect(isFinite(node.isoY)).toBe(true);
    }
  });

  it("plotsPerRow=0 is clamped to 1 — no division-by-zero in slotToGrid", () => {
    const g = layout(sample, { plotsPerRow: 0 });
    for (const node of Object.values(g.placed)) {
      expect(isFinite(node.isoX)).toBe(true);
      expect(isFinite(node.isoY)).toBe(true);
    }
  });

  it("roomsPerRow=0 is clamped to 1 — no division-by-zero in slotToGrid", () => {
    const g = layout(sample, { roomsPerRow: 0 });
    for (const node of Object.values(g.placed)) {
      expect(isFinite(node.isoX)).toBe(true);
      expect(isFinite(node.isoY)).toBe(true);
    }
  });

  it("negative baseCell is clamped to 1", () => {
    const g = layout(sample, { baseCell: -5 });
    for (const node of Object.values(g.placed)) {
      expect(isFinite(node.isoX)).toBe(true);
      expect(node.w).toBeGreaterThan(0);
    }
  });
});

describe("scene graph structure", () => {
  it("placed contains all active node ids from the sample fixture", () => {
    const graph = layout(sample, TOKENS);

    // Buildings
    expect(graph.placed["nook"]).toBeDefined();
    expect(graph.placed["workshop"]).toBeDefined();

    // Wings
    expect(graph.placed["wing:dev:sage"]).toBeDefined();
    expect(graph.placed["wing:personal:Personal"]).toBeDefined();
    expect(graph.placed["wing:unknown:orphan"]).toBeDefined();

    // Rooms
    expect(graph.placed["room:dev:sage:main"]).toBeDefined();
    expect(graph.placed["room:dev:sage:decisions"]).toBeDefined();
    expect(graph.placed["room:personal:Personal:core"]).toBeDefined();

    // Agents
    expect(graph.placed["dev-architect"]).toBeDefined();
    expect(graph.placed["dev-code-implementer"]).toBeDefined();

    // Family-wing synthetic nodes
    expect(graph.placed["family:dev"]).toBeDefined();

    // Plot
    expect(graph.placed["plot:sage"]).toBeDefined();

    // Grounds container
    expect(graph.placed["grounds"]).toBeDefined();
  });

  it("palace node has kind='palace'", () => {
    const graph = layout(sample, TOKENS);
    expect(graph.placed["nook"]!.kind).toBe("palace");
  });

  it("wing nodes have kind='wing'", () => {
    const graph = layout(sample, TOKENS);
    expect(graph.placed["wing:dev:sage"]!.kind).toBe("wing");
    expect(graph.placed["wing:personal:Personal"]!.kind).toBe("wing");
  });

  it("agent nodes have kind='agent-room'", () => {
    const graph = layout(sample, TOKENS);
    expect(graph.placed["dev-architect"]!.kind).toBe("agent-room");
    expect(graph.placed["dev-code-implementer"]!.kind).toBe("agent-room");
  });

  it("plot node has kind='plot' and dirty flag preserved", () => {
    const graph = layout(sample, TOKENS);
    const plot = graph.placed["plot:sage"] as PlacedNode;
    expect(plot.kind).toBe("plot");
    expect(plot.dirty).toBe(true); // fixture has dirty: true
  });

  it("meta.revision matches model.revision", () => {
    const graph = layout(sample, TOKENS);
    expect(graph.meta.revision).toBe(sample.revision);
  });

  it("ruins is empty when no prior ledger is supplied", () => {
    const graph = layout(sample, TOKENS);
    expect(graph.ruins).toHaveLength(0);
  });
});
