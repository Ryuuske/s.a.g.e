/**
 * Projected HTML labels for the 3D scene.
 *
 * Labels are HTML elements positioned via THREE's project() method — not
 * WebGL text. This keeps font quality high and avoids texture atlases.
 *
 * Layers:
 * - LOD 0-1: building nameplates (plate class)
 * - LOD >= 1: wing labels with progressive room/drawer counts
 * - LOD >= 2: room labels (mono class)
 *
 * WHERE: src/sage_mcp/estate/web/src/render3d/labels.ts
 */

import * as THREE from "three";
import type { LodLevel } from "./lod";

// ---------------------------------------------------------------------------
// Label pool (reuse DOM elements)
// ---------------------------------------------------------------------------

const labelPool: HTMLDivElement[] = [];

function getLabel(overlay: HTMLElement, index: number): HTMLDivElement {
  if (labelPool[index] == null) {
    const d = document.createElement("div");
    d.className = "lbl";
    overlay.appendChild(d);
    labelPool[index] = d;
  }
  return labelPool[index]!;
}

const tmp = new THREE.Vector3();

function projify(
  worldVec: THREE.Vector3,
  camera: THREE.Camera,
  canvas: HTMLCanvasElement,
): { x: number; y: number; vis: boolean } {
  tmp.copy(worldVec).project(camera);
  const r = canvas.getBoundingClientRect();
  return {
    x: r.left + (tmp.x * 0.5 + 0.5) * r.width,
    y: r.top + (-tmp.y * 0.5 + 0.5) * r.height,
    vis: tmp.z < 1,
  };
}

// ---------------------------------------------------------------------------
// Main update function
// ---------------------------------------------------------------------------

/**
 * Update all projected HTML labels for the current frame.
 *
 * @param overlay - the .overlay div that parents label elements
 * @param canvas - the WebGL canvas (for getBoundingClientRect)
 * @param camera - THREE camera for projection
 * @param level - current LOD level
 * @param buildings - array of top-level building groups (palace, workshop, grounds)
 * @param wingGroups - wing groups keyed by nodeId
 */
export function updateLabels(
  overlay: HTMLElement,
  canvas: HTMLCanvasElement,
  camera: THREE.Camera,
  level: LodLevel,
  buildings: THREE.Group[],
  wingGroups: Map<string, THREE.Group>,
): void {
  let n = 0;

  // Building nameplates (LOD 0–1)
  if (level <= 1) {
    for (const b of buildings) {
      const anchor = b.userData["anchor"] as THREE.Vector3 | undefined;
      if (anchor == null) continue;
      const worldAnchor = new THREE.Vector3().copy(anchor).add(b.position);
      const p = projify(worldAnchor, camera, canvas);
      const L = getLabel(overlay, n++);
      L.className = "lbl plate";
      L.textContent = (b.userData["title"] as string | undefined) ?? "";
      L.style.left = p.x + "px";
      L.style.top = p.y + "px";
      L.style.opacity = p.vis ? "1" : "0";
    }
  }

  // Wing labels (LOD >= 1)
  if (level >= 1) {
    for (const wg of wingGroups.values()) {
      const localAnchor = wg.userData["anchor"] as THREE.Vector3 | undefined;
      if (localAnchor == null) continue;
      const wpos = new THREE.Vector3().copy(localAnchor);
      wg.localToWorld(wpos);
      const p = projify(wpos, camera, canvas);
      const L = getLabel(overlay, n++);
      L.className = "lbl wing";
      const wingType = (wg.userData["wingType"] as string | undefined) ?? "unknown";
      const label = (wg.userData["label"] as string | undefined) ?? wingType;
      const roomCount = (wg.userData["roomCount"] as number | undefined) ?? 0;
      const drawerCount = (wg.userData["drawerCount"] as number | undefined) ?? 0;

      let countText = "";
      if (level >= 3) {
        // Leaf vocabulary per zoom.leaf_distinction_note: Nook wings → drawers.
        // workshop/tablinum leaf labels require userData["buildingId"] set on the
        // wing group in scene.ts — deferred to the Phase-3 vocabulary wiring.
        countText = `${drawerCount} drawers`;
      } else if (level >= 2) {
        countText = `${roomCount} rooms`;
      }
      L.innerHTML = `<span style="color:inherit">${escHtml(label)}</span>${countText ? `<span class="ct">${escHtml(countText)}</span>` : ""}`;
      L.style.left = p.x + "px";
      L.style.top = p.y + "px";
      L.style.opacity = p.vis ? "1" : "0";
    }
  }

  // Hide unused labels
  for (let i = n; i < labelPool.length; i++) {
    const lbl = labelPool[i];
    if (lbl != null) lbl.style.opacity = "0";
  }
}

function escHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
