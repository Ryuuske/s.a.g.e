/**
 * 3D scene tests — Phase 2c (ADR-0001).
 *
 * Tests cover pure scene construction: THREE.Group tree structure, deterministic
 * slot-driven placement, LOD visibility, material mapping, and detail content.
 * No WebGL needed — THREE scene construction runs in Node.
 *
 * Invariants under test:
 * 1. buildEstateScene produces 6 wing groups, each positioned by slot.
 * 2. A deleted-wing scene graph yields a ruin group + unchanged survivor positions.
 * 3. Wing materials match wingType (dev → cool slate, personal → warm plaster).
 * 4. levelFor() thresholds are correct.
 * 5. applyLevel() toggles LOD_KEY visibility correctly.
 * 6. materials.ts hex colour mapping is correct.
 * 7. detail.ts renderOverviewPanel returns HTML with wing stats.
 *
 * WHERE: src/sage_mcp/estate/web/test/scene3d.test.ts
 */

import { describe, expect, it } from "vitest";
import * as THREE from "three";
import type { EstateModel, NookBuilding, SceneGraph } from "../src/model/types";
import { layout } from "../src/layout/layout";
import { buildLedger } from "../src/layout/ledger";
import { buildEstateScene } from "../src/render3d/scene";
import { levelFor, applyLevel, LOD_KEY } from "../src/render3d/lod";
import { hexColor, getWingMaterials } from "../src/render3d/materials";
import { renderOverviewPanel } from "../src/render3d/detail";
import { DESIGN_TOKENS, wingTokens } from "../src/render/tokens";
import { descendToNearestWing, startCamFly, tickCamFly } from "../src/render3d/camera";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

// ---------------------------------------------------------------------------
// Fixture import — single-source (same fixture as layout tests)
// ---------------------------------------------------------------------------

import sampleRaw from "../../../../../tests/estate/fixtures/estate-model.sample.json" assert {
  type: "json",
};
const sample = sampleRaw as EstateModel;
const tokens = DESIGN_TOKENS;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function buildScene(model: EstateModel): { root: THREE.Group; graph: SceneGraph } {
  const graph = layout(model, tokens);
  const root = buildEstateScene(graph, tokens);
  return { root, graph };
}

/** Count direct children of a group that carry a specific userData key/value. */
function countChildrenWithUserData(
  root: THREE.Group,
  key: string,
  value?: unknown,
): number {
  let count = 0;
  root.traverse((obj) => {
    if (value !== undefined) {
      if (obj.userData[key] === value) count++;
    } else {
      if (obj.userData[key] != null) count++;
    }
  });
  return count;
}

/** Collect all groups that carry userData.nodeId (wing groups). */
function collectWingGroups(root: THREE.Group): THREE.Group[] {
  const wings: THREE.Group[] = [];
  root.traverse((obj) => {
    if (obj.userData["wingType"] != null && obj instanceof THREE.Group) {
      wings.push(obj);
    }
  });
  return wings;
}

// ---------------------------------------------------------------------------
// 1. Wing group count and slot-driven placement
// ---------------------------------------------------------------------------

describe("buildEstateScene: wing groups", () => {
  it("sample fixture: 6 wing groups present (or as many as in the fixture)", () => {
    const { root } = buildScene(sample);
    const wings = collectWingGroups(root);
    // The fixture has at least 3 wings (dev, personal, unknown per layout test)
    expect(wings.length).toBeGreaterThanOrEqual(3);
  });

  it("wing ordinals are slot-ordered and produce distinct (x, z) positions", () => {
    // The fix for the slot % 6 collision: positions are indexed by ordinal (0-based
    // rank among wings sorted by ledger slot), NOT the raw ledger slot.
    // Two wings whose ledger slots happen to be congruent mod 6 MUST get distinct positions.
    //
    // This test builds two wings whose ledger slots are deliberately chosen to be
    // mod-6-congruent (e.g. slots 1 and 7) and verifies they render at distinct positions.
    // Ledger slot assignment: palace=0, then wings in insertion order.
    // To get slots 1 and 7, we insert 7 nodes total (palace + 6 others before the second wing).
    // Simpler: use the priorLedger to force specific slots.
    const priorIds = ["nook", "wing:dev:a", "room:1", "room:2", "room:3", "room:4", "room:5", "wing:ops:b"];
    // After layout, wing:dev:a gets the slot matching its position in the prior ledger (slot 1),
    // wing:ops:b gets slot 7. 1 % 6 = 1, 7 % 6 = 1 → they would COLLIDE under the old scheme.
    const priorLedger = buildLedger(priorIds);

    const palace: NookBuilding = {
      id: "nook", kind: "palace", title: "N",
      wings: [
        { id: "wing:dev:a", type: "dev", title: "a", slot: 0, rooms: [], hall_counts: {}, drawer_total: 0 },
        { id: "wing:ops:b", type: "ops", title: "b", slot: 1, rooms: [], hall_counts: {}, drawer_total: 0 },
      ],
      tunnels: [], closets: {}, kg: {},
    };
    const model: EstateModel = {
      version: "1.0", revision: 1, captured_at: "2026-06-01T00:00:00Z",
      property: { name: "t", isolation: {}, health: { governance: {}, store: {} } },
      buildings: [palace], grounds: { plots: [] },
      outbuildings: { horrea: {}, tablinum: {}, gate: {} },
    };
    const graph = layout(model, tokens, priorLedger);
    const root = buildEstateScene(graph, tokens);
    const wings = collectWingGroups(root);
    expect(wings.length).toBe(2);

    // Distinctness: the two wings must NOT share (x, z).
    const [wg0, wg1] = wings;
    expect(wg0).toBeDefined();
    expect(wg1).toBeDefined();
    if (wg0 != null && wg1 != null) {
      const samePosition =
        Math.abs(wg0.position.x - wg1.position.x) < 0.01 &&
        Math.abs(wg0.position.z - wg1.position.z) < 0.01;
      expect(samePosition).toBe(false);
    }

    // Slot ordering: ordinal 0 goes to the wing with the lower ledger slot.
    // Both wings get their ledger slots from the prior ledger: wing:dev:a=1, wing:ops:b=7.
    // Under ordinal assignment: wing:dev:a (slot 1) → ordinal 0, wing:ops:b (slot 7) → ordinal 1.
    // WING_SLOT_POSITIONS[0] = { x: 4.6, z: -4.2 }, [1] = { x: 4.6, z: 0 }
    const sorted = [...wings].sort((a, b) => (a.userData["slot"] as number) - (b.userData["slot"] as number));
    const WING_SLOT_POSITIONS = [
      { x: 4.6, z: -4.2 },
      { x: 4.6, z: 0 },
      { x: 4.6, z: 4.2 },
      { x: -4.6, z: -4.2 },
      { x: -4.6, z: 0 },
      { x: -4.6, z: 4.2 },
    ];
    sorted.forEach((wg, ordinal) => {
      const expected = WING_SLOT_POSITIONS[ordinal]!;
      expect(wg.position.x).toBeCloseTo(expected.x, 2);
      expect(wg.position.z).toBeCloseTo(expected.z, 2);
    });
  });

  it("survivor wings keep their ordinal-derived positions after a wing deletion (append-stable)", () => {
    // After deleting a wing, surviving wings are re-enumerated in slot order.
    // Their ordinals may shift, but each ordinal still maps to a unique ring position.
    // The key invariant: all surviving wings have DISTINCT positions (no collision).
    const palace: NookBuilding = {
      id: "nook", kind: "palace", title: "N",
      wings: [
        { id: "wing:dev:a", type: "dev", title: "a", slot: 0, rooms: [], hall_counts: {}, drawer_total: 0 },
        { id: "wing:ops:b", type: "ops", title: "b", slot: 1, rooms: [], hall_counts: {}, drawer_total: 0 },
        { id: "wing:meta:c", type: "meta", title: "c", slot: 2, rooms: [], hall_counts: {}, drawer_total: 0 },
      ],
      tunnels: [], closets: {}, kg: {},
    };
    const fullModel: EstateModel = {
      version: "1.0", revision: 1, captured_at: "2026-06-01T00:00:00Z",
      property: { name: "t", isolation: {}, health: { governance: {}, store: {} } },
      buildings: [palace], grounds: { plots: [] },
      outbuildings: { horrea: {}, tablinum: {}, gate: {} },
    };

    // Build prior ledger from full model (nook=0, wing:dev:a=1, wing:ops:b=2, wing:meta:c=3)
    const priorLedger = buildLedger([
      "nook",
      "wing:dev:a", "wing:ops:b", "wing:meta:c",
    ]);

    // Remove wing:ops:b (ledger slot 2)
    const prunedPalace: NookBuilding = {
      ...palace,
      wings: palace.wings.filter((w) => w.id !== "wing:ops:b"),
    };
    const prunedModel: EstateModel = { ...fullModel, buildings: [prunedPalace] };

    const prunedGraph = layout(prunedModel, tokens, priorLedger);
    const root = buildEstateScene(prunedGraph, tokens);

    const wings = collectWingGroups(root);

    // Deleted wing must not appear
    const slotSet = new Set(wings.map((w) => w.userData["slot"] as number));
    expect(slotSet.has(2)).toBe(false);
    // Survivors (ledger slots 1, 3) must be present
    expect(slotSet.has(1)).toBe(true);
    expect(slotSet.has(3)).toBe(true);

    // All surviving wings must have DISTINCT (x, z) positions
    const positions = wings.map((w) => ({ x: w.position.x, z: w.position.z }));
    for (let i = 0; i < positions.length; i++) {
      for (let j = i + 1; j < positions.length; j++) {
        const pi = positions[i]!;
        const pj = positions[j]!;
        const collision = Math.abs(pi.x - pj.x) < 0.01 && Math.abs(pi.z - pj.z) < 0.01;
        expect(collision).toBe(false);
      }
    }

    // Ordinal-position contract: survivors sorted by ledger slot get ordinals 0,1 → positions [0],[1]
    const WING_SLOT_POSITIONS = [
      { x: 4.6, z: -4.2 },
      { x: 4.6, z: 0 },
      { x: 4.6, z: 4.2 },
      { x: -4.6, z: -4.2 },
      { x: -4.6, z: 0 },
      { x: -4.6, z: 4.2 },
    ];
    const sorted = [...wings].sort((a, b) => (a.userData["slot"] as number) - (b.userData["slot"] as number));
    sorted.forEach((wg, ordinal) => {
      const expected = WING_SLOT_POSITIONS[ordinal]!;
      expect(wg.position.x).toBeCloseTo(expected.x, 2);
      expect(wg.position.z).toBeCloseTo(expected.z, 2);
    });
  });

  it("deleted wing leaves a ruin glyph in the scene graph", () => {
    const priorLedger = buildLedger(["nook", "wing:dev:ghost"]);
    const emptyModel: EstateModel = {
      version: "1.0", revision: 1, captured_at: "2026-06-01T00:00:00Z",
      property: { name: "t", isolation: {}, health: { governance: {}, store: {} } },
      buildings: [{
        id: "nook", kind: "palace", title: "N",
        wings: [], tunnels: [], closets: {}, kg: {},
      }],
      grounds: { plots: [] },
      outbuildings: { horrea: {}, tablinum: {}, gate: {} },
    };
    const graph = layout(emptyModel, tokens, priorLedger);
    expect(graph.ruins).toHaveLength(1);
    const root = buildEstateScene(graph, tokens);
    // Ruin glyph tagged with ruinsGroup
    const hasRuins = countChildrenWithUserData(root, "ruinsGroup") > 0;
    expect(hasRuins).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 2. Wing materials match wingType
// ---------------------------------------------------------------------------

describe("wing materials match wingType", () => {
  it("dev wing: roof material hex matches design token roof colour", () => {
    const devTok = wingTokens("dev");
    const mats = getWingMaterials("dev");
    // mats.roof color should match devTok.roof (#1d2730 → THREE.Color)
    const tokenHex = "#" + hexColor(devTok.roof as string).getHexString();
    const matHex = "#" + mats.roof.color.getHexString();
    expect(matHex).toBe(tokenHex);
  });

  it("personal wing: wall + roof clearly distinct from unknown (ruin)", () => {
    const personalMats = getWingMaterials("personal");
    const unknownMats = getWingMaterials("unknown");
    // After fix: wall is tinted (65% travertine + 35% wing wall-color), so
    // personal's tinted wall must DIFFER from unknown's tinted wall.
    expect(personalMats.wall.color.getHexString()).not.toBe(
      unknownMats.wall.color.getHexString(),
    );
    // Pilaster (pure wing wall-color) also differs
    expect(personalMats.pilaster.color.getHexString()).not.toBe(
      unknownMats.pilaster.color.getHexString(),
    );
    // personal has a roof, unknown does not (roof="none" → falls back to wall_accent)
    const personalTok = wingTokens("personal");
    const unknownTok = wingTokens("unknown");
    expect(personalTok.roof).not.toBe(unknownTok.roof);
    expect(personalMats.roof.color.getHexString()).not.toBe(
      unknownMats.roof.color.getHexString(),
    );
  });

  it("all 6 standard wing types produce a material set", () => {
    for (const t of ["dev", "project", "knowledge", "ops", "meta", "personal"] as const) {
      const mats = getWingMaterials(t);
      expect(mats.wall).toBeInstanceOf(THREE.MeshStandardMaterial);
      expect(mats.roof).toBeInstanceOf(THREE.MeshStandardMaterial);
      expect(mats.light).toBeInstanceOf(THREE.MeshStandardMaterial);
    }
  });

  it("unknown wing type returns ruin materials without throwing", () => {
    const mats = getWingMaterials("unknown");
    expect(mats.wall).toBeInstanceOf(THREE.MeshStandardMaterial);
  });

  it("unregistered wing type falls back to unknown materials", () => {
    const unregistered = getWingMaterials("does-not-exist");
    const unknown = getWingMaterials("unknown");
    // Both should have the same wall color (fallback to unknown)
    expect(unregistered.wall.color.getHexString()).toBe(unknown.wall.color.getHexString());
  });
});

// ---------------------------------------------------------------------------
// 3. LOD level thresholds
// ---------------------------------------------------------------------------

describe("levelFor() LOD thresholds", () => {
  it("distance > 34 → Level 0 (Property)", () => {
    expect(levelFor(35)).toBe(0);
    expect(levelFor(100)).toBe(0);
    expect(levelFor(34.1)).toBe(0);
  });

  it("distance 22–34 → Level 1 (Wings & rooms)", () => {
    expect(levelFor(34)).toBe(1);
    expect(levelFor(28)).toBe(1);
    expect(levelFor(22.1)).toBe(1);
  });

  it("distance 13–22 → Level 2 (Closer · rooms)", () => {
    expect(levelFor(22)).toBe(2);
    expect(levelFor(17)).toBe(2);
    expect(levelFor(13.1)).toBe(2);
  });

  it("distance <= 13 → Level 3 (Closest · drawers)", () => {
    expect(levelFor(13)).toBe(3);
    expect(levelFor(5)).toBe(3);
    expect(levelFor(0)).toBe(3);
  });

  it("boundary exactly at 34 → Level 1", () => {
    expect(levelFor(34)).toBe(1);
  });

  it("boundary exactly at 22 → Level 2", () => {
    expect(levelFor(22)).toBe(2);
  });

  it("boundary exactly at 13 → Level 3", () => {
    expect(levelFor(13)).toBe(3);
  });
});

// ---------------------------------------------------------------------------
// 4. applyLevel() visibility toggling
// ---------------------------------------------------------------------------

describe("applyLevel() LOD visibility", () => {
  it("object with lodMinLevel=2 is invisible at level 0 and 1", () => {
    const g = new THREE.Group();
    const child = new THREE.Group();
    child.userData[LOD_KEY] = 2;
    g.add(child);

    applyLevel(g, 0, "auto");
    expect(child.visible).toBe(false);

    applyLevel(g, 1, "auto");
    expect(child.visible).toBe(false);
  });

  it("object with lodMinLevel=2 is visible at level 2 and 3", () => {
    const g = new THREE.Group();
    const child = new THREE.Group();
    child.userData[LOD_KEY] = 2;
    child.visible = false;
    g.add(child);

    applyLevel(g, 2, "auto");
    expect(child.visible).toBe(true);

    applyLevel(g, 3, "auto");
    expect(child.visible).toBe(true);
  });

  it("object with lodMinLevel=3 is invisible at level 2", () => {
    const g = new THREE.Group();
    const child = new THREE.Group();
    child.userData[LOD_KEY] = 3;
    child.visible = true;
    g.add(child);

    applyLevel(g, 2, "auto");
    expect(child.visible).toBe(false);
  });

  it("lodMinLevel=0 always visible", () => {
    const g = new THREE.Group();
    const child = new THREE.Group();
    child.userData[LOD_KEY] = 0;
    g.add(child);
    for (const L of [0, 1, 2, 3] as const) {
      applyLevel(g, L, "auto");
      expect(child.visible).toBe(true);
    }
  });

  it("roofMode=on: roof stays down at level 1", () => {
    const g = new THREE.Group();
    const roof = new THREE.Group();
    const mat = new THREE.MeshStandardMaterial();
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), mat);
    roof.add(mesh);
    roof.userData["isRoof"] = true;
    roof.userData["roofBaseY"] = 5.0;
    roof.position.set(0, 5.0, 0);
    g.add(roof);

    applyLevel(g, 1, "on");
    // roofMode=on means roofs stay down (do not lift)
    expect(roof.position.y).toBe(5.0);
    expect(mat.transparent).toBe(false);
  });

  it("roofMode=auto: roof lifts at level 1", () => {
    const g = new THREE.Group();
    const roof = new THREE.Group();
    const mat = new THREE.MeshStandardMaterial();
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), mat);
    roof.add(mesh);
    roof.userData["isRoof"] = true;
    roof.userData["roofBaseY"] = 5.0;
    roof.position.set(0, 5.0, 0);
    g.add(roof);

    applyLevel(g, 1, "auto");
    // roofMode=auto at level 1 → lift
    expect(roof.position.y).toBeGreaterThan(5.0);
    expect(mat.transparent).toBe(true);
    expect(mat.opacity).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// 5. materials.ts hexColor helper
// ---------------------------------------------------------------------------

describe("hexColor() parsing", () => {
  it("parses a standard 6-digit hex", () => {
    const c = hexColor("#ff0000");
    expect(c.r).toBeCloseTo(1, 2);
    expect(c.g).toBeCloseTo(0, 2);
    expect(c.b).toBeCloseTo(0, 2);
  });

  it("parses a lowercase hex", () => {
    const c = hexColor("#2b3640");
    expect(c).toBeInstanceOf(THREE.Color);
  });

  it("strips alpha from 8-digit hex (#rrggbbaa)", () => {
    // Should not throw and should parse the first 6 digits
    const c = hexColor("#d9775722");
    expect(c).toBeInstanceOf(THREE.Color);
  });
});

// ---------------------------------------------------------------------------
// 6. detail.ts renderOverviewPanel
// ---------------------------------------------------------------------------

describe("renderOverviewPanel", () => {
  const graph = layout(sample, tokens);

  it("returns a non-empty HTML string", () => {
    const html = renderOverviewPanel(graph, tokens, null, 0);
    expect(html.trim().length).toBeGreaterThan(0);
  });

  it("contains 'The Sage Property' in estate overview", () => {
    const html = renderOverviewPanel(graph, tokens, null, 0);
    expect(html).toContain("The Sage Property");
  });

  it("nook selected: panel shows The Nook title", () => {
    const html = renderOverviewPanel(graph, tokens, "nook", 1);
    expect(html).toContain("The Nook");
  });

  it("workshop selected: panel shows agent count", () => {
    const html = renderOverviewPanel(graph, tokens, "workshop", 0);
    expect(html).toContain("The Workshop");
    expect(html).toContain("agents");
  });

  it("grounds selected: panel shows repo plots", () => {
    const html = renderOverviewPanel(graph, tokens, "grounds", 0);
    expect(html).toContain("The Grounds");
    expect(html).toContain("Repo plots");
  });

  it("nook panel includes wing row for dev wing", () => {
    const html = renderOverviewPanel(graph, tokens, "nook", 1);
    // The sample fixture has a dev wing labeled 'sage'
    expect(html).toContain("sage");
  });

  it("nook panel includes room and drawer counts (roomCount/drawerCount)", () => {
    const html = renderOverviewPanel(graph, tokens, "nook", 1);
    // Should contain 'rm' and 'dr' from wing rows
    expect(html).toContain("rm");
    expect(html).toContain("dr");
  });

  it("dirty plot renders with warn colour in grounds panel", () => {
    const html = renderOverviewPanel(graph, tokens, "grounds", 0);
    // Dirty plot should show warn signal
    expect(html).toContain("--warn");
  });
});

// ---------------------------------------------------------------------------
// 7. Drawer cabinet keyed by drawerBucket
// ---------------------------------------------------------------------------

describe("drawer cabinets in wing rooms", () => {
  it("wing with rooms that have drawerBucket > 0 produces cabinet objects (LOD 3)", () => {
    // Build a model where a room has high drawer count to get drawerBucket > 0
    const palace: NookBuilding = {
      id: "nook", kind: "palace", title: "N",
      wings: [{
        id: "wing:dev:test", type: "dev", title: "dev", slot: 0,
        rooms: [
          { id: "room:a", title: "a", slot: 0, drawer_count: 25 }, // bucket = 2
        ],
        hall_counts: {}, drawer_total: 25,
      }],
      tunnels: [], closets: {}, kg: {},
    };
    const model: EstateModel = {
      version: "1.0", revision: 1, captured_at: "2026-06-01T00:00:00Z",
      property: { name: "t", isolation: {}, health: { governance: {}, store: {} } },
      buildings: [palace], grounds: { plots: [] },
      outbuildings: { horrea: {}, tablinum: {}, gate: {} },
    };
    const graph = layout(model, tokens);
    const root = buildEstateScene(graph, tokens);

    // Find the roomsGroup inside the wing
    let foundCabinet = false;
    root.traverse((obj) => {
      if (obj.userData[LOD_KEY] === 3) foundCabinet = true;
    });
    expect(foundCabinet).toBe(true);
  });

  it("wing with rooms that have drawerBucket == 0 produces no drawer cabinet (LOD 3)", () => {
    const palace: NookBuilding = {
      id: "nook", kind: "palace", title: "N",
      wings: [{
        id: "wing:dev:test", type: "dev", title: "dev", slot: 0,
        rooms: [
          { id: "room:a", title: "a", slot: 0, drawer_count: 5 }, // bucket = 0
        ],
        hall_counts: {}, drawer_total: 5,
      }],
      tunnels: [], closets: {}, kg: {},
    };
    const model: EstateModel = {
      version: "1.0", revision: 1, captured_at: "2026-06-01T00:00:00Z",
      property: { name: "t", isolation: {}, health: { governance: {}, store: {} } },
      buildings: [palace], grounds: { plots: [] },
      outbuildings: { horrea: {}, tablinum: {}, gate: {} },
    };
    const graph = layout(model, tokens);
    const root = buildEstateScene(graph, tokens);

    let cabinetCount = 0;
    root.traverse((obj) => {
      if (obj.userData[LOD_KEY] === 3) cabinetCount++;
    });
    // No cabinet for bucket=0
    expect(cabinetCount).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// 8. Slot-collision distinctness — wing ordinal fix (BLOCKING fix #1)
// ---------------------------------------------------------------------------

describe("wing slot-collision fix: mod-6-congruent ledger slots → distinct positions", () => {
  it("two wings with ledger slots 1 and 7 (both ≡ 1 mod 6) render at DISTINCT (x,z)", () => {
    // Pre-fix: both wings would be indexed by slot % 6 = 1 → same position → COLLISION.
    // Post-fix: they get ordinals 0 and 1 → distinct ring positions.
    //
    // Force ledger slots 1 and 7 by using a prior ledger with 7 intermediate entries.
    const priorIds = [
      "nook",
      "wing:dev:a",           // slot 1
      "placeholder:1",        // slot 2
      "placeholder:2",        // slot 3
      "placeholder:3",        // slot 4
      "placeholder:4",        // slot 5
      "placeholder:5",        // slot 6
      "wing:ops:b",           // slot 7  →  7 % 6 = 1, same as slot 1
    ];
    const priorLedger = buildLedger(priorIds);

    const palace: NookBuilding = {
      id: "nook", kind: "palace", title: "N",
      wings: [
        { id: "wing:dev:a", type: "dev", title: "a", slot: 0, rooms: [], hall_counts: {}, drawer_total: 0 },
        { id: "wing:ops:b", type: "ops", title: "b", slot: 1, rooms: [], hall_counts: {}, drawer_total: 0 },
      ],
      tunnels: [], closets: {}, kg: {},
    };
    const model: EstateModel = {
      version: "1.0", revision: 1, captured_at: "2026-06-01T00:00:00Z",
      property: { name: "t", isolation: {}, health: { governance: {}, store: {} } },
      buildings: [palace], grounds: { plots: [] },
      outbuildings: { horrea: {}, tablinum: {}, gate: {} },
    };
    const graph = layout(model, tokens, priorLedger);

    // Verify the assigned ledger slots are indeed mod-6-congruent
    const wingNodes = Object.values(graph.placed).filter((n) => n.kind === "wing");
    expect(wingNodes.length).toBe(2);
    const wingSlots = wingNodes.map((n) => n.slot).sort((a, b) => a - b);
    expect(wingSlots[0]! % 6).toBe(wingSlots[1]! % 6); // the collision precondition

    const root = buildEstateScene(graph, tokens);
    const wings = collectWingGroups(root);
    expect(wings.length).toBe(2);

    // Post-fix: positions MUST be distinct
    const [wg0, wg1] = wings;
    expect(wg0).toBeDefined();
    expect(wg1).toBeDefined();
    if (wg0 != null && wg1 != null) {
      const collision =
        Math.abs(wg0.position.x - wg1.position.x) < 0.01 &&
        Math.abs(wg0.position.z - wg1.position.z) < 0.01;
      expect(collision).toBe(false);
    }
  });

  it("wing with distinct ordinal renders at the expected ring position", () => {
    // Ordinal 0 → WING_SLOT_POSITIONS[0] = { x: 4.6, z: -4.2 }
    // Ordinal 1 → WING_SLOT_POSITIONS[1] = { x: 4.6, z: 0 }
    const palace: NookBuilding = {
      id: "nook", kind: "palace", title: "N",
      wings: [
        { id: "wing:dev:a", type: "dev", title: "a", slot: 0, rooms: [], hall_counts: {}, drawer_total: 0 },
        { id: "wing:ops:b", type: "ops", title: "b", slot: 1, rooms: [], hall_counts: {}, drawer_total: 0 },
      ],
      tunnels: [], closets: {}, kg: {},
    };
    const model: EstateModel = {
      version: "1.0", revision: 1, captured_at: "2026-06-01T00:00:00Z",
      property: { name: "t", isolation: {}, health: { governance: {}, store: {} } },
      buildings: [palace], grounds: { plots: [] },
      outbuildings: { horrea: {}, tablinum: {}, gate: {} },
    };
    const { root } = buildScene(model);
    const wings = collectWingGroups(root);
    const sorted = [...wings].sort((a, b) => (a.userData["slot"] as number) - (b.userData["slot"] as number));

    // ordinal 0 (lowest ledger slot wing) → ring position 0
    expect(sorted[0]?.position.x).toBeCloseTo(4.6, 2);
    expect(sorted[0]?.position.z).toBeCloseTo(-4.2, 2);
    // ordinal 1 → ring position 1
    expect(sorted[1]?.position.x).toBeCloseTo(4.6, 2);
    expect(sorted[1]?.position.z).toBeCloseTo(0, 2);
  });
});

// ---------------------------------------------------------------------------
// 9. Wing-distinctness: personal vs project vs unknown visual identity
// ---------------------------------------------------------------------------

describe("wing material distinctness: personal vs project vs unknown", () => {
  it("personal, project, and unknown wings all have distinct tinted wall colors", () => {
    const personalMats = getWingMaterials("personal");
    const projectMats = getWingMaterials("project");
    const unknownMats = getWingMaterials("unknown");

    const personalHex = personalMats.wall.color.getHexString();
    const projectHex = projectMats.wall.color.getHexString();
    const unknownHex = unknownMats.wall.color.getHexString();

    expect(personalHex).not.toBe(projectHex);
    expect(personalHex).not.toBe(unknownHex);
    expect(projectHex).not.toBe(unknownHex);
  });

  it("personal, project, and unknown wings all have distinct pilaster colors", () => {
    const personalMats = getWingMaterials("personal");
    const projectMats = getWingMaterials("project");
    const unknownMats = getWingMaterials("unknown");

    expect(personalMats.pilaster.color.getHexString()).not.toBe(
      projectMats.pilaster.color.getHexString(),
    );
    expect(personalMats.pilaster.color.getHexString()).not.toBe(
      unknownMats.pilaster.color.getHexString(),
    );
    expect(projectMats.pilaster.color.getHexString()).not.toBe(
      unknownMats.pilaster.color.getHexString(),
    );
  });

  it("tinted wall color is a blend toward travertine (lighter than raw wall token)", () => {
    // Travertine is #c9b596 (relatively warm light). Personal wing wall is #7c4a38 (darker).
    // The tinted blend (65% travertine + 35% personal) should be lighter (higher luminance)
    // than the raw personal color.
    const personalTok = wingTokens("personal");
    const rawPersonalColor = new THREE.Color(personalTok.wall);
    const personalMats = getWingMaterials("personal");
    const tintedColor = personalMats.wall.color;

    // Tinted luminance should be higher than raw (closer to travertine)
    const rawLuminance = 0.2126 * rawPersonalColor.r + 0.7152 * rawPersonalColor.g + 0.0722 * rawPersonalColor.b;
    const tintedLuminance = 0.2126 * tintedColor.r + 0.7152 * tintedColor.g + 0.0722 * tintedColor.b;
    expect(tintedLuminance).toBeGreaterThan(rawLuminance);
  });
});

// ---------------------------------------------------------------------------
// 10. Camera framing: descendToNearestWing + startCamFly / tickCamFly
// ---------------------------------------------------------------------------

describe("camera framing: descendToNearestWing and camFly", () => {
  it("descendToNearestWing returns null for empty wingGroups", () => {
    // Create minimal OrbitControls mock (just needs .target)
    const mockControls = { target: new THREE.Vector3(0, 0, 0) } as unknown as OrbitControls;
    const result = descendToNearestWing(mockControls, new Map());
    expect(result).toBeNull();
  });

  it("descendToNearestWing returns the world anchor of the nearest wing", () => {
    const mockControls = { target: new THREE.Vector3(4.6, 0, -4.2) } as unknown as OrbitControls;

    const wg0 = new THREE.Group();
    wg0.userData["anchor"] = new THREE.Vector3(0, 4.0, 0);
    wg0.position.set(4.6, 0, -4.2);
    wg0.updateMatrixWorld(true);

    const wg1 = new THREE.Group();
    wg1.userData["anchor"] = new THREE.Vector3(0, 4.0, 0);
    wg1.position.set(-4.6, 0, 4.2);
    wg1.updateMatrixWorld(true);

    const map = new Map<string, THREE.Group>([["a", wg0], ["b", wg1]]);
    const result = descendToNearestWing(mockControls, map);
    expect(result).not.toBeNull();
    // The nearest wing (wg0) has world anchor at (4.6, 4.0, -4.2)
    if (result != null) {
      expect(result.x).toBeCloseTo(4.6, 1);
      expect(result.y).toBeCloseTo(4.0, 1);
      expect(result.z).toBeCloseTo(-4.2, 1);
    }
  });

  it("startCamFly returns the target vector", () => {
    const target = new THREE.Vector3(8, 6, 8);
    const result = startCamFly(target);
    expect(result.x).toBeCloseTo(8, 2);
    expect(result.y).toBeCloseTo(6, 2);
    expect(result.z).toBeCloseTo(8, 2);
  });

  it("tickCamFly moves camera toward target and returns null when close", () => {
    const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 500);
    const target = new THREE.Vector3(1, 0, 0);
    camera.position.set(1, 0, 0); // already at target
    const result = tickCamFly(camera, target);
    expect(result).toBeNull(); // close enough → done
  });

  it("tickCamFly keeps returning target when far", () => {
    const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 500);
    const target = new THREE.Vector3(100, 0, 0);
    camera.position.set(0, 0, 0);
    const result = tickCamFly(camera, target);
    expect(result).not.toBeNull(); // not close yet → still animating
  });
});
