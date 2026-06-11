/**
 * app.ts — entry point for the 3D estate dashboard.
 *
 * Mounts the cockpit shell, creates the WebGL renderer, wires the render/LOD
 * loop, interaction events, and detail panel.
 *
 * Uses the fixture model (estate-model.sample.json) for development/demo.
 * In production, the SceneGraph would be pushed from the Python backend.
 *
 * WHERE: src/sage_mcp/estate/web/src/render3d/app.ts
 */

import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { SceneGraph } from "../model/types";
import type { EstateModel } from "../model/types";
import { layout } from "../layout/layout";
import { DESIGN_TOKENS } from "../render/tokens";
import { buildEstateScene } from "./scene";
import { levelFor, applyLevel, LEVEL_NAMES } from "./lod";
import type { LodLevel } from "./lod";
import { updateLabels } from "./labels";
import { renderOverviewPanel } from "./detail";
import {
  createPerspectiveCamera,
  setupOrbitControls,
  resizeRenderer,
  DEF_CAM,
  DEF_TARGET,
  startFly,
  tickFly,
  startCamFly,
  tickCamFly,
  descendToNearestWing,
} from "./camera";

// ---------------------------------------------------------------------------
// Load fixture model (replaced by live data in production)
// ---------------------------------------------------------------------------

// The fixture is imported at build time by Vite
import sampleRaw from "../../../../../../tests/estate/fixtures/estate-model.sample.json";
const fixtureModel = sampleRaw as EstateModel;

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

const tokens = DESIGN_TOKENS;
const sceneGraph: SceneGraph = layout(fixtureModel, tokens);

// ---------------------------------------------------------------------------
// DOM helper — fail fast with a clear message on a missing element id
// ---------------------------------------------------------------------------

function el<T extends HTMLElement>(id: string): T {
  const e = document.getElementById(id);
  if (e == null) throw new Error(`missing #${id}`);
  return e as T;
}

// ---------------------------------------------------------------------------
// Renderer + scene
// ---------------------------------------------------------------------------

const canvas = el<HTMLCanvasElement>("scene");
const overlay = el<HTMLElement>("overlay");
const panelBody = el<HTMLElement>("panelBody");
const crumbHere = el<HTMLElement>("crumbHere");
const lodName = el<HTMLElement>("lodName");
const roofBtn = el<HTMLButtonElement>("roofBtn");
const resetBtn = el<HTMLButtonElement>("resetBtn");

const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;

renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.1;

const threeScene = new THREE.Scene();
threeScene.fog = new THREE.Fog(0x0b1014, 40, 95);

const camera = createPerspectiveCamera();
const controls = setupOrbitControls(camera, canvas);

// Lighting
threeScene.add(new THREE.HemisphereLight(0x3a342c, 0x0a0d0b, 0.6));
threeScene.add(new THREE.AmbientLight(0x46506a, 0.22));

const key = new THREE.DirectionalLight(0xffd49a, 1.55);
key.position.set(-20, 26, 12);
key.castShadow = true;
key.shadow.mapSize.set(2048, 2048);
key.shadow.camera.near = 1;
key.shadow.camera.far = 100;
key.shadow.camera.left = -38;
key.shadow.camera.right = 38;
key.shadow.camera.top = 38;
key.shadow.camera.bottom = -38;
key.shadow.bias = -0.0004;
threeScene.add(key);

const fill = new THREE.DirectionalLight(0x4a6a9a, 0.42);
fill.position.set(22, 14, -16);
threeScene.add(fill);

const warmGlow = new THREE.PointLight(0xffb070, 0.5, 34);
warmGlow.position.set(0, 5, 0);
threeScene.add(warmGlow);

// Build estate scene from SceneGraph
const estateRoot = buildEstateScene(sceneGraph, tokens);
threeScene.add(estateRoot);

const buildings = estateRoot.userData["buildings"] as THREE.Group[];
const wingGroups = estateRoot.userData["wingGroups"] as Map<string, THREE.Group>;

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let currentLevel: LodLevel = -1 as LodLevel;
let roofMode: "auto" | "on" | "off" = "auto";
let selected: string | null = null;
let animFly: ReturnType<typeof startFly> | null = null;
let camFlyState: THREE.Vector3 | null = null;
/** World-space target.target position of the last snapped wing anchor (for re-entry drift detection). */
let lastSnapTarget: THREE.Vector3 | null = null;
/** Threshold: re-fire descend if controls.target has drifted this far from last snap. */
const DESCEND_REFIRE_THRESHOLD = 2.0;

// ---------------------------------------------------------------------------
// LOD application
// ---------------------------------------------------------------------------

const LEVEL_NAMES_MAP = LEVEL_NAMES;

function applyLodLevel(L: LodLevel): void {
  if ((L as number) === (currentLevel as number)) return;
  currentLevel = L;

  lodName.textContent = LEVEL_NAMES_MAP[L];
  for (let i = 0; i < 4; i++) {
    const el = document.getElementById(`st${i}`);
    if (el != null) el.className = "st" + (i <= L ? " on" : "");
  }
  crumbHere.textContent = L === 0
    ? "The Sage Property"
    : "The Nook · " + LEVEL_NAMES_MAP[L];

  applyLevel(estateRoot, L, roofMode);

  // Descend-to-nearest-wing at LOD 3.
  // Re-fire whenever entering LOD 3 AND controls.target has drifted > threshold
  // from the last snapped wing anchor (handles re-entry after orbiting away).
  if (L >= 3) {
    const hasDrifted =
      lastSnapTarget == null ||
      controls.target.distanceTo(lastSnapTarget) > DESCEND_REFIRE_THRESHOLD;
    if (hasDrifted) {
      const wingAnchor = descendToNearestWing(controls, wingGroups);
      if (wingAnchor != null) {
        // Fly controls.target to wing anchor
        animFly = startFly(controls, wingAnchor);
        lastSnapTarget = wingAnchor.clone();
        // Fly camera to a 3/4 view offset from the wing anchor (≈8,6,8)
        const camTarget = wingAnchor.clone().add(new THREE.Vector3(8, 6, 8));
        camFlyState = startCamFly(camTarget);
      }
    }
  } else {
    // Reset drift tracker when leaving LOD 3 so next entry always re-fires
    lastSnapTarget = null;
  }
}

// ---------------------------------------------------------------------------
// Interaction
// ---------------------------------------------------------------------------

const ray = new THREE.Raycaster();
const mouse = new THREE.Vector2();
let downXY: [number, number] | null = null;

canvas.addEventListener("pointerdown", (e) => {
  downXY = [e.clientX, e.clientY];
});

canvas.addEventListener("pointerup", (e) => {
  if (downXY == null) return;
  const dx = e.clientX - downXY[0];
  const dy = e.clientY - downXY[1];
  if (Math.hypot(dx, dy) < 5) {
    const r = canvas.getBoundingClientRect();
    mouse.x = ((e.clientX - r.left) / r.width) * 2 - 1;
    mouse.y = -((e.clientY - r.top) / r.height) * 2 + 1;
    ray.setFromCamera(mouse, camera);
    const hits = ray.intersectObjects(buildings, true);
    if (hits.length > 0) {
      let o: THREE.Object3D = hits[0]!.object;
      while (o.parent != null && o.userData["buildingId"] == null) {
        o = o.parent;
      }
      const id = o.userData["buildingId"] as string | undefined;
      if (id != null) {
        selectBuilding(id, o as THREE.Group);
      }
    }
  }
  downXY = null;
});

function selectBuilding(id: string, obj: THREE.Group): void {
  selected = selected === id ? null : id;
  renderPanel();
  const pos = obj.position.clone();
  pos.y = 2.2;
  animFly = startFly(controls, pos);
}

roofBtn.addEventListener("click", () => {
  roofMode = roofMode === "auto" ? "on" : roofMode === "on" ? "off" : "auto";
  roofBtn.textContent = "Roofs: " + roofMode;
  currentLevel = -1 as LodLevel; // force reapply
  applyLodLevel(levelFor(camera.position.distanceTo(controls.target)));
});

resetBtn.addEventListener("click", () => {
  selected = null;
  renderPanel();
  animFly = startFly(controls, DEF_TARGET.clone());
  camFlyState = startCamFly(DEF_CAM.clone());
});

// Building card clicks in panel
panelBody.addEventListener("click", (e) => {
  const card = (e.target as HTMLElement).closest(".estcard") as HTMLElement | null;
  if (card != null) {
    const id = card.dataset["id"];
    if (id != null) {
      const obj = buildings.find((b) => b.userData["buildingId"] === id);
      if (obj != null) selectBuilding(id, obj);
    }
  }
});

// ---------------------------------------------------------------------------
// Panel rendering
// ---------------------------------------------------------------------------

function renderPanel(): void {
  panelBody.innerHTML = renderOverviewPanel(sceneGraph, tokens, selected, currentLevel);
}

// ---------------------------------------------------------------------------
// Resize
// ---------------------------------------------------------------------------

window.addEventListener("resize", () => {
  resizeRenderer(renderer, camera, canvas);
});
resizeRenderer(renderer, camera, canvas);

// ---------------------------------------------------------------------------
// Render loop
// ---------------------------------------------------------------------------

renderPanel();
applyLodLevel(0);

// Honour prefers-reduced-motion (tokens.accessibility.motion): fly animations
// become instant cuts rather than lerped transitions.
const REDUCED_MOTION =
  typeof window !== "undefined" &&
  typeof window.matchMedia === "function" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function tick(): void {
  requestAnimationFrame(tick);

  // Target fly animation (controls.target) — snap under reduced-motion.
  if (animFly != null) {
    if (REDUCED_MOTION) {
      controls.target.copy(animFly.to);
      animFly = null;
    } else {
      animFly = tickFly(animFly, controls);
    }
  }

  // Camera position fly (used by descend-to-wing and reset) — snap under reduced-motion.
  if (camFlyState != null) {
    if (REDUCED_MOTION) {
      camera.position.copy(camFlyState);
      camFlyState = null;
    } else {
      camFlyState = tickCamFly(camera, camFlyState);
    }
  }

  controls.update();

  // Compute distance using distanceTo (NOT getDistance — removed in r128+)
  const dist = camera.position.distanceTo(controls.target);
  applyLodLevel(levelFor(dist));

  estateRoot.updateMatrixWorld();
  updateLabels(overlay, canvas, camera, currentLevel, buildings, wingGroups);

  renderer.render(threeScene, camera);
}

tick();

// ---------------------------------------------------------------------------
// Expose controls on OrbitControls for resize use
// ---------------------------------------------------------------------------

void (controls as OrbitControls);
