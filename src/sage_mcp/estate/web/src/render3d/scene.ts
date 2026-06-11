/**
 * 3D scene builder — pure function: SceneGraph + DesignTokens → THREE.Group.
 *
 * NO renderer, NO canvas, NO DOM — pure scene graph construction.
 * This module is unit-testable in Node (THREE scene construction runs fine
 * without WebGL).
 *
 * Architecture rules:
 * - Reads ONLY SceneGraph (ADR-0001 decoupling). Never reads EstateModel.
 * - slot → 3D position is deterministic (slot drives placement, NOT isoX/isoY).
 * - Append-stability: a deleted wing leaves a ruin gap; survivors never reflow.
 *
 * Wing → villa position mapping (decision: documented here as it wasn't pinned
 * in the spec):
 *   Wings are enumerated in ledger-slot order and assigned a 0-based ORDINAL
 *   among wings (NOT the raw ledger slot — rooms/drawers consume intervening
 *   ledger slots, so indexing by raw slot would collide). The ordinal indexes a
 *   ring of villa positions around the great hall (inner ring for ordinals 0-5,
 *   an outer ring for 6-11). Same ordinal ⇒ same position ⇒ deterministic +
 *   append-stable. NOTE (follow-up): >12 wings reuse a ring position; when live
 *   data has many repo-wings, group them by wing-TYPE into 6 villa-ranges
 *   (ADR-0005 bucketing) rather than one villa-wing per repo.
 *
 * WHERE: src/sage_mcp/estate/web/src/render3d/scene.ts
 */

import * as THREE from "three";
import type { PlacedNode, RuinNode, SceneGraph } from "../model/types";
import type { DesignTokens } from "../render/tokens";
import { LOD_KEY } from "./lod";
import {
  MAT_TRAVERTINE,
  MAT_TRAVERTINE_HI,
  MAT_COLUMN,
  MAT_PALACE_WALL,
  MAT_PALACE_ROOF,
  MAT_PALACE_ACCENT,
  MAT_PALACE_CORNICE,
  MAT_PALACE_BASE,
  MAT_PEDIMENT,
  MAT_IMPLUVIUM,
  MAT_COURT,
  MAT_WORKSHOP_WALL,
  MAT_WORKSHOP_ROOF,
  MAT_WORKSHOP_ACCENT,
  MAT_WORKSHOP_LIGHT,
  MAT_SOIL,
  MAT_HEDGE,
  MAT_RUIN_WALL,
  MAT_RUIN_ACCENT,
  MAT_MARBLE,
  getWingMaterials,
  stdMat,
  emisMat,
} from "./materials";

// ---------------------------------------------------------------------------
// Slot → villa position (deterministic, ordinal-based)
// ---------------------------------------------------------------------------

/**
 * 6 fixed (x, z) offsets for wing ring positions 0–5.
 * Index is the 0-based ORDINAL among wings (by ascending ledger-slot order),
 * NOT the raw ledger slot number.
 *
 * Rationale: ledger slots are monotonic across all node types (palace, rooms,
 * drawers consume intervening slots), so two wings whose ledger slots are
 * congruent mod 6 would collide if indexed directly. By enumerating only the
 * wing nodes in slot order and assigning each a 0-based ordinal, we guarantee
 * each of the first 6 wings occupies a unique ring position.
 *
 * For > 6 wings a second ring (outer radius) is added to prevent any collision.
 */
const WING_SLOT_POSITIONS: Array<{ x: number; z: number }> = [
  { x: 4.6, z: -4.2 },  // ordinal 0 — right-front
  { x: 4.6, z: 0 },     // ordinal 1 — right-centre
  { x: 4.6, z: 4.2 },   // ordinal 2 — right-back
  { x: -4.6, z: -4.2 }, // ordinal 3 — left-front
  { x: -4.6, z: 0 },    // ordinal 4 — left-centre
  { x: -4.6, z: 4.2 },  // ordinal 5 — left-back
];

/** Outer ring for ordinals 6–11 (scale by 1.65 to avoid collision with inner ring). */
const WING_OUTER_POSITIONS: Array<{ x: number; z: number }> = WING_SLOT_POSITIONS.map(
  (p) => ({ x: p.x * 1.65, z: p.z * 1.65 }),
);

function wingOrdinalPosition(ordinal: number): { x: number; z: number } {
  if (ordinal < WING_SLOT_POSITIONS.length) {
    return WING_SLOT_POSITIONS[ordinal]!;
  }
  // Second ring: ordinals 6–11
  const outerOrdinal = ordinal % WING_SLOT_POSITIONS.length;
  return WING_OUTER_POSITIONS[outerOrdinal]!;
}

/**
 * Build a slot → ordinal map from the wings in graph.placed.
 * Wings are enumerated in ascending ledger-slot order; each receives a
 * 0-based ordinal. This ensures two wings with mod-6-congruent ledger slots
 * get DISTINCT ring positions.
 */
function buildWingOrdinalMap(
  placed: Record<string, import("../model/types").PlacedNode>,
): Map<string, number> {
  const wingEntries = Object.entries(placed)
    .filter(([, n]) => n.kind === "wing")
    .sort(([, a], [, b]) => a.slot - b.slot);
  const map = new Map<string, number>();
  wingEntries.forEach(([id], i) => map.set(id, i));
  return map;
}

// ---------------------------------------------------------------------------
// Primitive helpers (matching the demo's geometry vocabulary)
// ---------------------------------------------------------------------------

/** Standard wall block with shadow casting. */
function box(w: number, h: number, d: number, mat: THREE.Material): THREE.Mesh {
  const geo = new THREE.BoxGeometry(w, h, d);
  const mesh = new THREE.Mesh(geo, mat);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

/** Roman column: plinth + tapered shaft + capital. */
function column(h: number, r = 0.16): THREE.Group {
  const g = new THREE.Group();

  const plinth = box(r * 3, 0.18, r * 3, MAT_COLUMN);
  plinth.position.set(0, 0.09, 0);
  g.add(plinth);

  const shaft = new THREE.Mesh(
    new THREE.CylinderGeometry(r * 0.86, r, h - 0.36, 12),
    MAT_COLUMN,
  );
  shaft.position.set(0, 0.18 + (h - 0.36) / 2, 0);
  shaft.castShadow = true;
  g.add(shaft);

  const cap = box(r * 2.6, 0.18, r * 2.6, MAT_TRAVERTINE_HI);
  cap.position.set(0, h - 0.09, 0);
  g.add(cap);

  return g;
}

/** Colonnade row along Z (n columns) + entablature beam. */
function colonnade(n: number, span: number, h: number): THREE.Group {
  const g = new THREE.Group();
  for (let i = 0; i < n; i++) {
    const c = column(h);
    c.position.z = (i - (n - 1) / 2) * (span / Math.max(1, n - 1));
    g.add(c);
  }
  const beam = box(0.5, 0.34, span + 0.8, MAT_TRAVERTINE_HI);
  beam.position.set(0, h + 0.17, 0);
  beam.castShadow = true;
  g.add(beam);
  return g;
}

/** Low terracotta gable roof (ridge along Z). Returns a group with a roof mesh. */
function roofGable(
  width: number,
  length: number,
  h: number,
  roofMat: THREE.Material,
): THREE.Group {
  const g = new THREE.Group();
  const ov = width * 0.16;
  const sh = new THREE.Shape();
  sh.moveTo(-width / 2 - ov, 0);
  sh.lineTo(width / 2 + ov, 0);
  sh.lineTo(0, h);
  sh.lineTo(-width / 2 - ov, 0);
  const rg = new THREE.ExtrudeGeometry(sh, { depth: length + 0.6, bevelEnabled: false });
  rg.translate(0, 0, -(length + 0.6) / 2);
  const roof = new THREE.Mesh(rg, roofMat);
  roof.castShadow = true;
  roof.receiveShadow = true;
  g.add(roof);
  return g;
}

interface HallResult {
  group: THREE.Group;
  roofGroup: THREE.Group;
  roofBaseY: number;
}

/**
 * Hall block: travertine walls + cornice + terracotta roof + warm windows.
 * Returns group plus a separate roofGroup for lift animation.
 */
function hall(
  width: number,
  height: number,
  length: number,
  roofMat: THREE.Material,
  lightColor: string | null,
  opts: {
    roofH?: number;
    winRows?: number;
    wallMat?: THREE.Material;
  } = {},
): HallResult {
  const g = new THREE.Group();

  const wallMat = opts.wallMat ?? MAT_TRAVERTINE;
  const wallMesh = box(width, height, length, wallMat);
  wallMesh.position.set(0, height / 2, 0);
  g.add(wallMesh);

  const cornice = box(width + 0.3, 0.3, length + 0.3, MAT_PALACE_CORNICE);
  cornice.position.set(0, height, 0);
  cornice.castShadow = true;
  g.add(cornice);

  const baseMesh = box(width + 0.4, 0.4, length + 0.4, MAT_PALACE_BASE);
  baseMesh.position.set(0, 0.2, 0);
  baseMesh.receiveShadow = true;
  g.add(baseMesh);

  // Roof group (separate so LOD/roof-lift can target it)
  const roofH = opts.roofH ?? Math.min(width * 0.34, 1.7);
  const roofGroup = roofGable(width, length, roofH, roofMat);
  const roofBaseY = height + 0.15;
  roofGroup.position.set(0, roofBaseY, 0);
  roofGroup.userData["isRoof"] = true;
  roofGroup.userData["roofBaseY"] = roofBaseY;
  g.add(roofGroup);

  // Windows (warm emissive panes)
  if (lightColor != null) {
    const rows = opts.winRows ?? 3;
    const winMat = emisMat(lightColor);
    for (const s of [-1, 1]) {
      for (let i = 0; i < rows; i++) {
        const win = new THREE.Mesh(new THREE.PlaneGeometry(0.55, 0.85), winMat);
        win.position.set(
          s * (width / 2 + 0.02),
          height * 0.5,
          (i - (rows - 1) / 2) * (length / (rows + 0.3)),
        );
        win.rotation.y = s > 0 ? Math.PI / 2 : -Math.PI / 2;
        g.add(win);
      }
    }
  }

  return { group: g, roofGroup, roofBaseY };
}

// ---------------------------------------------------------------------------
// Palace (The Nook — Roman domus)
// ---------------------------------------------------------------------------

function buildPalace(): THREE.Group {
  const nook = new THREE.Group();
  nook.userData["buildingId"] = "nook";
  nook.userData["title"] = "The Nook";
  nook.userData["roman"] = "Domus";
  nook.userData["sub"] = "wings → rooms → drawers";

  // Great hall (central atrium)
  const { group: greatHallGroup, roofGroup: hallRoof } = hall(
    5, 4.2, 7,
    MAT_PALACE_ROOF, "#9be7c2",
    { roofH: 2.0, winRows: 3 },
  );
  nook.add(greatHallGroup);

  // Verdigris ridge accent
  const vRidge = box(0.2, 0.2, 7.4, MAT_PALACE_ACCENT);
  vRidge.position.set(0, 4.2 + 0.15 + 2.0, 0);
  nook.add(vRidge);

  // Grand pedimented portico at front (+Z)
  const portico = new THREE.Group();
  portico.position.set(0, 0, 7 / 2 + 1.5);
  for (let i = 0; i < 4; i++) {
    const c = column(3.0, 0.2);
    c.position.x = (i - 1.5) * 1.3;
    portico.add(c);
  }
  const archi = box(5.6, 0.4, 1.6, MAT_TRAVERTINE_HI);
  archi.position.set(0, 3.0 + 0.2, 0);
  archi.castShadow = true;
  portico.add(archi);

  // Pediment triangle
  const pedSh = new THREE.Shape();
  pedSh.moveTo(-2.9, 0); pedSh.lineTo(2.9, 0); pedSh.lineTo(0, 1.3); pedSh.lineTo(-2.9, 0);
  const pedMesh = new THREE.Mesh(
    new THREE.ExtrudeGeometry(pedSh, { depth: 1.4, bevelEnabled: false }),
    MAT_PEDIMENT,
  );
  pedMesh.position.set(0, 3.4, -0.7);
  pedMesh.castShadow = true;
  portico.add(pedMesh);
  nook.add(portico);

  // Peristyle colonnades + courtyard
  for (const s of [-1, 1]) {
    const col = colonnade(5, 8, 2.4);
    col.position.set(s * 4.6, 0, 7 / 2 + 5.2);
    nook.add(col);
  }
  const courtFloor = box(9.2, 0.25, 8, MAT_COURT);
  courtFloor.position.set(0, 0.12, 7 / 2 + 5.2);
  courtFloor.receiveShadow = true;
  nook.add(courtFloor);

  const impluvium = new THREE.Mesh(new THREE.BoxGeometry(3.2, 0.2, 2.2), MAT_IMPLUVIUM);
  impluvium.position.set(0, 0.26, 7 / 2 + 5.2);
  nook.add(impluvium);

  // Anchor point for label projection
  nook.userData["anchor"] = new THREE.Vector3(0, 6.4, 0);

  // The hall's roof group is tagged for roof-lift; we need to keep the reference
  void hallRoof;

  return nook;
}

// ---------------------------------------------------------------------------
// Wings
// ---------------------------------------------------------------------------

/**
 * Build one wing group from a placed wing node.
 * Position is driven by ordinal → WING_SLOT_POSITIONS (deterministic).
 * isoX/isoY are NOT read — those are 2.5D layout coords.
 *
 * @param ordinal  0-based index of this wing among all wings sorted by ledger slot.
 *                 Used to index WING_SLOT_POSITIONS (NOT the raw ledger slot).
 */
function buildWing(nodeId: string, node: PlacedNode, ordinal: number): THREE.Group {
  const wingType = node.wingType ?? "unknown";
  const mats = getWingMaterials(wingType);

  const wg = new THREE.Group();
  wg.userData["nodeId"] = nodeId;
  wg.userData["wingType"] = wingType;
  wg.userData["label"] = node.label;
  wg.userData["slot"] = node.slot;
  wg.userData["ordinal"] = ordinal;
  wg.userData["roomCount"] = node.roomCount ?? 0;
  wg.userData["drawerCount"] = node.drawerCount ?? 0;

  // Hall block (wing body)
  const lightColor = mats.light.emissive instanceof THREE.Color
    ? "#" + mats.light.emissive.getHexString()
    : "#ffffff";
  const { group: wingHallGroup, roofGroup } = hall(
    3.4, 2.6, 4.4,
    mats.roof, lightColor,
    { roofH: 1.4, winRows: 2, wallMat: mats.wall },
  );
  wg.add(wingHallGroup);

  // Little porch: 2 columns facing the courtyard
  for (const cx of [-1, 1]) {
    const c = column(2.2, 0.13);
    c.position.set(cx * 1.0, 0, 4.4 / 2 + 0.5);
    wg.add(c);
  }

  // Colored frieze band (wing identity)
  const frieze = box(3.5, 0.28, 0.12, mats.frieze);
  frieze.position.set(0, 2.6 - 0.2, 4.4 / 2 + 0.07);
  wg.add(frieze);

  // Pilaster accent strip (base course — wing identity cue at LOD 0)
  const pilaster = box(3.6, 0.35, 4.5, mats.pilaster);
  pilaster.position.set(0, 0.175, 0);
  wg.add(pilaster);

  // Ordinal-driven position (NOT raw ledger slot — see buildWingOrdinalMap)
  const pos = wingOrdinalPosition(ordinal);
  wg.rotation.y = Math.PI / 2; // ranges run along X, fronts face the spine
  wg.position.set(pos.x, 0, pos.z);

  // Anchor for label projection (in local space, before rotation)
  wg.userData["anchor"] = new THREE.Vector3(0, 4.0, 0);

  // The roofGroup is already tagged in hall(); pass through so applyLevel works
  void roofGroup;

  return wg;
}

// ---------------------------------------------------------------------------
// Workshop (Fabrica)
// ---------------------------------------------------------------------------

function buildWorkshop(): THREE.Group {
  const workshop = new THREE.Group();
  workshop.userData["buildingId"] = "workshop";
  workshop.userData["title"] = "The Workshop";
  workshop.userData["roman"] = "Fabrica";
  workshop.userData["sub"] = "agents & tooling";

  const { group: fabGroup } = hall(
    5.2, 3.4, 9,
    MAT_WORKSHOP_ROOF, "#ffd98a",
    { roofH: 1.8, winRows: 4, wallMat: MAT_WORKSHOP_WALL },
  );
  workshop.add(fabGroup);

  // Gold ridge accent
  const goldRidge = box(0.18, 0.18, 9.4, MAT_WORKSHOP_ACCENT);
  goldRidge.position.set(0, 3.4 + 0.15 + 1.8, 0);
  workshop.add(goldRidge);

  // Front colonnade
  const wcol = colonnade(6, 8.4, 2.6);
  wcol.position.set(-5.2 / 2 - 0.7, 0, 0);
  workshop.add(wcol);

  workshop.userData["anchor"] = new THREE.Vector3(0, 5.6, 0);
  workshop.position.set(15, 0, -1);

  // Agent rooms inside workshop (LOD >= 2)
  void MAT_WORKSHOP_LIGHT; // available for future agent-room lighting

  return workshop;
}

// ---------------------------------------------------------------------------
// Grounds (repo plots — hortus)
// ---------------------------------------------------------------------------

function buildGrounds(allNodes: Record<string, PlacedNode>): THREE.Group {
  const grounds = new THREE.Group();
  grounds.userData["buildingId"] = "grounds";
  grounds.userData["title"] = "The Grounds";
  grounds.userData["roman"] = "Peristylium";

  grounds.position.set(1, 0, 15);

  // Collect plot nodes sorted by slot — slot-stable ordinal drives col/row so
  // deleting a middle plot never reflows surviving siblings (ADR-0005).
  const plots = Object.entries(allNodes)
    .filter(([, n]) => n.kind === "plot")
    .sort(([, a], [, b]) => a.slot - b.slot);

  plots.forEach(([, plotNode], ordinal) => {
    const col = ordinal % 3;
    const row = Math.floor(ordinal / 3);
    const plot = new THREE.Group();

    const soil = box(2.3, 0.35, 2.3, MAT_SOIL);
    soil.position.set(0, 0.17, 0);
    soil.receiveShadow = true;
    plot.add(soil);

    // Hedges on four sides
    for (const [sx, sz] of [[-1, 0], [1, 0], [0, -1], [0, 1]] as Array<[number, number]>) {
      const hw = box(sx !== 0 ? 0.22 : 2.3, 0.5, sz !== 0 ? 0.22 : 2.3, MAT_HEDGE);
      hw.position.set(sx * 1.04, 0.4, sz * 1.04);
      hw.castShadow = true;
      plot.add(hw);
    }

    // Post / marker (dirty plots get a taller post)
    const dirty = plotNode.dirty === true;
    const postMat = dirty ? stdMat("#7a6228") : stdMat("#6a6a5a");
    const postH = dirty ? 1.0 : 0.7;
    const post = box(0.7, postH, 0.7, postMat);
    post.position.set(0, postH / 2, 0);
    post.castShadow = true;
    plot.add(post);

    plot.position.set((col - 1) * 3.0, 0, (row - 0.5) * 3.0);
    grounds.add(plot);
  });

  grounds.userData["anchor"] = new THREE.Vector3(1, 2.6, 15);

  return grounds;
}

// ---------------------------------------------------------------------------
// Ruin glyphs (for ruins in SceneGraph)
// ---------------------------------------------------------------------------

function buildRuinGlyph(ruin: RuinNode): THREE.Group {
  const g = new THREE.Group();
  g.userData["ruinId"] = ruin.deletedId;
  g.userData["slot"] = ruin.slot;

  // Roofless walls: left wall + right wall (no top face)
  const wallH = 1.5;
  const left = box(0.15, wallH, 2.2, MAT_RUIN_WALL);
  left.position.set(-1.0, wallH / 2, 0);
  g.add(left);

  const right = box(0.15, wallH, 2.2, MAT_RUIN_ACCENT);
  right.position.set(1.0, wallH / 2, 0);
  g.add(right);

  // Scaffold pole
  const pole = box(0.06, wallH * 1.2, 0.06, MAT_RUIN_ACCENT);
  pole.position.set(0, wallH * 0.6, 1.2);
  g.add(pole);

  // Position: ruins row below palace zone
  const col = ruin.slot % 8;
  const row = Math.floor(ruin.slot / 8);
  g.position.set(col * 3.5 - 12, 0, 28 + row * 4);

  return g;
}

// ---------------------------------------------------------------------------
// Main export: buildEstateScene
// ---------------------------------------------------------------------------

/**
 * buildEstateScene — pure function: SceneGraph + DesignTokens → THREE.Group.
 *
 * Returns a root group containing:
 * - palace group (nook) at origin
 * - wing groups (6 ring wings around the hall, keyed by slot)
 * - workshop group at (15, 0, -1)
 * - grounds group at (1, 0, 15)
 * - ruin glyphs (gap markers for deleted nodes)
 * - platform + grid
 *
 * Placement is slot-driven and deterministic. isoX/isoY are NOT read.
 */
export function buildEstateScene(
  graph: SceneGraph,
  _tokens: DesignTokens,
): THREE.Group {
  const root = new THREE.Group();
  root.userData["sceneRoot"] = true;

  // Platform + grid
  const platform = new THREE.Mesh(
    new THREE.BoxGeometry(58, 0.6, 52),
    new THREE.MeshStandardMaterial({ color: 0x10160f, roughness: 1 }),
  );
  platform.position.set(0, -0.3, 0);
  platform.receiveShadow = true;
  root.add(platform);

  const grid = new THREE.GridHelper(58, 58, 0x22312a, 0x161f1a);
  grid.position.set(0, 0.02, 0);
  root.add(grid);

  // Perimeter walls
  for (const [px, pz, pw, pd] of [
    [0, -26, 58, 0.6], [0, 26, 58, 0.6], [-29, 0, 0.6, 52], [29, 0, 0.6, 52],
  ] as Array<[number, number, number, number]>) {
    const w = new THREE.Mesh(
      new THREE.BoxGeometry(pw, 1.2, pd),
      new THREE.MeshStandardMaterial({ color: 0x18201a, roughness: 1 }),
    );
    w.position.set(px, 0.6, pz);
    w.castShadow = true;
    w.receiveShadow = true;
    root.add(w);
  }

  // Palace
  const palace = buildPalace();
  root.add(palace);

  // Wings — keyed by nodeId, positioned by ordinal (NOT raw ledger slot)
  // The ordinal map enumerates wings in ascending ledger-slot order so that
  // two wings with mod-6-congruent ledger slots get distinct ring positions.
  const wingOrdinals = buildWingOrdinalMap(graph.placed);
  const wingGroups = new Map<string, THREE.Group>();
  for (const [nodeId, node] of Object.entries(graph.placed)) {
    if (node.kind === "wing") {
      const ordinal = wingOrdinals.get(nodeId) ?? 0;
      const wg = buildWing(nodeId, node, ordinal);
      buildRoomsForWingByNodeId(nodeId, node, graph.placed, wg);
      palace.add(wg);
      wingGroups.set(nodeId, wg);
    }
  }

  // Workshop
  const workshop = buildWorkshop();
  // Add agent rooms inside workshop (LOD >= 2)
  buildAgentRooms(graph.placed, workshop);
  root.add(workshop);

  // Grounds
  const grounds = buildGrounds(graph.placed);
  root.add(grounds);

  // Ruin glyphs
  if (graph.ruins.length > 0) {
    const ruinsGroup = new THREE.Group();
    ruinsGroup.userData["ruinsGroup"] = true;
    for (const ruin of graph.ruins) {
      ruinsGroup.add(buildRuinGlyph(ruin));
    }
    root.add(ruinsGroup);
  }

  // Expose buildings array on root for interaction
  root.userData["buildings"] = [palace, workshop, grounds];
  root.userData["wingGroups"] = wingGroups;

  return root;
}

// ---------------------------------------------------------------------------
// Room builder variant that accepts nodeId instead of using closure
// ---------------------------------------------------------------------------

function buildRoomsForWingByNodeId(
  wingNodeId: string,
  wingNode: PlacedNode,
  allNodes: Record<string, PlacedNode>,
  wingGroup: THREE.Group,
): void {
  const mats = getWingMaterials(wingNode.wingType ?? "unknown");

  // Find room children, sorted by slot for append-stable ordinal placement.
  // Ordinal (position in this sorted array) drives X offset, not raw slot number,
  // so deleting a middle room leaves a gap and survivors never reflow (ADR-0005).
  const roomEntries = Object.entries(allNodes)
    .filter(([, n]) => n.kind === "room" && n.parentId === wingNodeId)
    .sort(([, a], [, b]) => a.slot - b.slot);

  const roomsGroup = new THREE.Group();
  roomsGroup.userData[LOD_KEY] = 2;
  roomsGroup.visible = false;
  roomsGroup.userData["kind"] = "rooms";

  const roomCount = roomEntries.length;
  roomEntries.forEach(([, roomNode], ordinal) => {
    const rm = box(0.7, 0.9, 3.0, MAT_MARBLE);
    rm.position.set((ordinal - (roomCount - 1) / 2) * 0.85, 0.5, 0);
    rm.castShadow = true;
    roomsGroup.add(rm);

    // Drawer cabinet (LOD >= 3)
    const drawerBucket = roomNode.drawerBucket ?? 0;
    if (drawerBucket > 0) {
      const cols = 2;
      const rws = Math.min(6, drawerBucket);
      const cab = new THREE.Group();
      cab.userData[LOD_KEY] = 3;
      cab.visible = false;
      for (let dc = 0; dc < cols; dc++) {
        for (let dr = 0; dr < rws; dr++) {
          const dw = new THREE.Mesh(new THREE.PlaneGeometry(0.26, 0.1), mats.light);
          dw.position.set(
            (ordinal - (roomCount - 1) / 2) * 0.85 + (dc - 0.5) * 0.3,
            0.2 + dr * 0.13,
            1.51,
          );
          cab.add(dw);
        }
      }
      roomsGroup.add(cab);
    }
  });

  wingGroup.add(roomsGroup);
  wingGroup.userData["roomsGroup"] = roomsGroup;
}

// ---------------------------------------------------------------------------
// Agent rooms inside workshop
// ---------------------------------------------------------------------------

function buildAgentRooms(
  allNodes: Record<string, PlacedNode>,
  workshopGroup: THREE.Group,
): void {
  const agentRooms = Object.entries(allNodes).filter(
    ([, n]) => n.kind === "agent-room",
  );
  if (agentRooms.length === 0) return;

  const agentsGroup = new THREE.Group();
  agentsGroup.userData[LOD_KEY] = 2;
  agentsGroup.visible = false;
  agentsGroup.userData["kind"] = "agentRooms";

  // Sort by slot so ordinal position is stable across agent deletions (ADR-0005).
  agentRooms.sort(([, a], [, b]) => a.slot - b.slot);

  // Position agents in rows inside the fabrica (slot-stable ordinal → col/row)
  agentRooms.forEach(([nodeId, agentNode], ordinal) => {
    const col = ordinal % 4;
    const row = Math.floor(ordinal / 4);
    const agentMesh = box(1.2, 0.6, 1.2, MAT_WORKSHOP_WALL);
    agentMesh.position.set(
      (col - 1.5) * 1.4,
      0.3,
      (row - 1) * 1.5 - 1,
    );
    agentMesh.userData["nodeId"] = nodeId;
    agentMesh.userData["label"] = agentNode.label;

    // Warm window (agent active light)
    const win = new THREE.Mesh(
      new THREE.PlaneGeometry(0.55, 0.4),
      MAT_WORKSHOP_LIGHT,
    );
    win.position.set(0, 0.3, 0.61);
    agentMesh.add(win);

    agentsGroup.add(agentMesh);
  });

  workshopGroup.add(agentsGroup);
}

