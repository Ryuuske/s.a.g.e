/**
 * Camera setup and fly-to-node helpers for the 3D renderer.
 *
 * Wraps THREE.PerspectiveCamera + OrbitControls configuration.
 * Provides fly-to-node animation and descend-to-nearest-wing focus at LOD >= 3.
 *
 * NOTE on OrbitControls API:
 * - r128+ removed getDistance() — use camera.position.distanceTo(controls.target).
 * - We never call Object.assign on THREE.Vector3/Object3D.position (read-only).
 *   Use .position.set() or .position.copy().
 *
 * WHERE: src/sage_mcp/estate/web/src/render3d/camera.ts
 */

import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

// ---------------------------------------------------------------------------
// Default camera position (estate overview)
// ---------------------------------------------------------------------------

export const DEF_CAM = new THREE.Vector3(24, 21, 30);
export const DEF_TARGET = new THREE.Vector3(0, 1.5, 0);

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

/**
 * Configure OrbitControls with the standard estate settings.
 * Call after creating the renderer/canvas.
 */
export function setupOrbitControls(
  camera: THREE.PerspectiveCamera,
  canvas: HTMLElement,
): OrbitControls {
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.target.copy(DEF_TARGET);
  controls.minDistance = 4.5;
  controls.maxDistance = 72;
  controls.maxPolarAngle = Math.PI * 0.495;
  controls.minPolarAngle = 0.12;
  controls.rotateSpeed = 0.62;
  controls.zoomSpeed = 1.6;
  return controls;
}

/**
 * Create a PerspectiveCamera at the default estate overview position.
 * aspect is updated on resize via resizeRenderer().
 */
export function createPerspectiveCamera(aspect = 1): THREE.PerspectiveCamera {
  const camera = new THREE.PerspectiveCamera(40, aspect, 0.1, 500);
  camera.position.copy(DEF_CAM);
  return camera;
}

// ---------------------------------------------------------------------------
// Animated fly-to-node
// ---------------------------------------------------------------------------

export interface FlyState {
  from: THREE.Vector3;
  to: THREE.Vector3;
  t: number; // 0..1 easing parameter
}

/** Start a fly-to animation toward a target position. Returns new FlyState. */
export function startFly(
  controls: OrbitControls,
  target: THREE.Vector3,
): FlyState {
  return { from: controls.target.clone(), to: target, t: 0 };
}

/** Advance a fly animation one tick. Returns null when complete. */
export function tickFly(
  fly: FlyState,
  controls: OrbitControls,
): FlyState | null {
  fly.t = Math.min(1, fly.t + 0.06);
  const e = 1 - Math.pow(1 - fly.t, 3); // cubic ease-out
  controls.target.lerpVectors(fly.from, fly.to, e);
  if (fly.t >= 1) return null;
  return fly;
}

/** Fly the camera position to a target point (for descend-to-wing). */
export function startCamFly(to: THREE.Vector3): THREE.Vector3 {
  return to.clone();
}

/** Advance camera position toward target. Returns null when close enough. */
export function tickCamFly(
  camera: THREE.PerspectiveCamera,
  target: THREE.Vector3,
): THREE.Vector3 | null {
  camera.position.lerp(target, 0.08);
  if (camera.position.distanceTo(target) < 0.1) return null;
  return target;
}

// ---------------------------------------------------------------------------
// Descend-to-nearest-wing focus (LOD >= 3 refinement)
// ---------------------------------------------------------------------------

/**
 * When the user reaches LOD >= 3, auto-focus on the nearest wing to the
 * current orbit target. Returns a fly target (controls.target destination)
 * pointing at the nearest wing's anchor, or null if no wings exist.
 */
export function descendToNearestWing(
  controls: OrbitControls,
  wingGroups: Map<string, THREE.Group>,
): THREE.Vector3 | null {
  if (wingGroups.size === 0) return null;

  let nearestDist = Infinity;
  let nearestAnchor: THREE.Vector3 | null = null;

  for (const wg of wingGroups.values()) {
    const worldAnchor = new THREE.Vector3();
    // Compute world position of the anchor (local → world)
    const localAnchor = wg.userData["anchor"] as THREE.Vector3 | undefined;
    if (localAnchor == null) continue;
    worldAnchor.copy(localAnchor);
    wg.localToWorld(worldAnchor);

    const dist = worldAnchor.distanceTo(controls.target);
    if (dist < nearestDist) {
      nearestDist = dist;
      nearestAnchor = worldAnchor.clone();
    }
  }

  return nearestAnchor;
}

// ---------------------------------------------------------------------------
// Resize handler
// ---------------------------------------------------------------------------

/**
 * Update renderer size + camera aspect on container resize.
 * Uses the canvas's parent element bounding rect.
 */
export function resizeRenderer(
  renderer: THREE.WebGLRenderer,
  camera: THREE.PerspectiveCamera,
  canvas: HTMLCanvasElement,
): void {
  const parent = canvas.parentElement;
  if (parent == null) return;
  const rect = parent.getBoundingClientRect();
  if (rect.width === 0 || rect.height === 0) return;
  renderer.setSize(rect.width, rect.height, false);
  camera.aspect = rect.width / rect.height;
  camera.updateProjectionMatrix();
}
