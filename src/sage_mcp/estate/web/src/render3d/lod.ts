/**
 * LOD (Level of Detail) — semantic zoom thresholds and visibility logic.
 *
 * Thresholds are camera-distance bands (distance from camera to OrbitControls target).
 * Crossing a band reveals/hides a layer. Matches the 4-level semantic zoom in
 * estate-design-tokens.json (zoom.levels).
 *
 * WHERE: src/sage_mcp/estate/web/src/render3d/lod.ts
 */

import * as THREE from "three";

// ---------------------------------------------------------------------------
// LOD level type
// ---------------------------------------------------------------------------

export type LodLevel = 0 | 1 | 2 | 3;

// ---------------------------------------------------------------------------
// Distance thresholds (camera.position.distanceTo(controls.target))
// NOTE: uses distanceTo, NOT OrbitControls.getDistance() which was removed in r128+.
// ---------------------------------------------------------------------------

const THRESHOLD_L1 = 34; // farther than this → Level 0 (Property)
const THRESHOLD_L2 = 22; // between 22–34 → Level 1 (Wings & rooms)
const THRESHOLD_L3 = 13; // between 13–22 → Level 2 (Closer · rooms)
                          // closer than 13 → Level 3 (Closest · drawers)

/**
 * Compute the LOD level from the current camera distance to orbit target.
 *
 * @param distance - camera.position.distanceTo(controls.target)
 * @returns LodLevel (0–3)
 */
export function levelFor(distance: number): LodLevel {
  if (distance > THRESHOLD_L1) return 0;
  if (distance > THRESHOLD_L2) return 1;
  if (distance > THRESHOLD_L3) return 2;
  return 3;
}

// ---------------------------------------------------------------------------
// Level names (for HUD display)
// ---------------------------------------------------------------------------

export const LEVEL_NAMES: Record<LodLevel, string> = {
  0: "Property",
  1: "Wings & rooms",
  2: "Closer · rooms",
  3: "Closest · drawers",
};

// ---------------------------------------------------------------------------
// Scene visibility groups — userData keys used by scene.ts to tag objects
// ---------------------------------------------------------------------------

/**
 * userData keys placed on THREE.Object3D nodes by scene.ts so applyLevel()
 * can toggle visibility without re-traversing the scene type hierarchy.
 *
 * - lodMinLevel: minimum LOD level at which this object is visible.
 *   Object is visible when currentLevel >= lodMinLevel.
 */
export const LOD_KEY = "lodMinLevel" as const;

/**
 * Apply LOD visibility to the entire scene group.
 *
 * Traverses all objects that carry userData.lodMinLevel and sets .visible
 * based on whether the current level meets or exceeds the threshold.
 *
 * Also handles roof-lift: objects tagged with userData.isRoof are
 * translated up + made transparent when level >= 1 (unless roofMode='on').
 */
export function applyLevel(
  sceneRoot: THREE.Group,
  level: LodLevel,
  roofMode: "auto" | "on" | "off",
): void {
  const roofsOff =
    roofMode === "off" || (roofMode === "auto" && level >= 1);

  sceneRoot.traverse((obj) => {
    // LOD visibility
    const minLevel = obj.userData[LOD_KEY] as number | undefined;
    if (minLevel != null) {
      obj.visible = level >= minLevel;
    }

    // Roof lift
    if (obj.userData["isRoof"] === true) {
      const baseY = obj.userData["roofBaseY"];
      if (typeof baseY !== "number") return;
      if (roofsOff) {
        obj.position.y = baseY + 7;
        if (obj instanceof THREE.Mesh && obj.material instanceof THREE.MeshStandardMaterial) {
          obj.material.transparent = true;
          obj.material.opacity = 0;
        }
        // Recurse into roof group children
        obj.traverse((child) => {
          if (child instanceof THREE.Mesh && child.material instanceof THREE.MeshStandardMaterial) {
            child.material.transparent = true;
            child.material.opacity = 0;
          }
        });
      } else {
        obj.position.y = baseY;
        obj.traverse((child) => {
          if (child instanceof THREE.Mesh && child.material instanceof THREE.MeshStandardMaterial) {
            child.material.transparent = false;
            child.material.opacity = 1;
          }
        });
      }
    }
  });
}
