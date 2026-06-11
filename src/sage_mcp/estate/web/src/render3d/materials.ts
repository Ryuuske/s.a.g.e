/**
 * Material factory — maps design tokens to THREE.Material instances.
 *
 * Architecture rule: reads ONLY design tokens — never EstateModel directly.
 * WingType tokens drive wing materials; building tokens drive palace/workshop.
 *
 * WHERE: src/sage_mcp/estate/web/src/render3d/materials.ts
 */

import * as THREE from "three";
import type { WingTypeName } from "../render/tokens";
import { wingTokens, DESIGN_TOKENS } from "../render/tokens";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Parse a CSS hex colour string (#rrggbb or #rrggbbaa) to a THREE.Color. */
export function hexColor(hex: string): THREE.Color {
  // THREE.Color handles #rrggbb; strip alpha component if present (#rrggbbaa)
  const clean = hex.length === 9 ? hex.slice(0, 7) : hex;
  return new THREE.Color(clean);
}

/** Create a MeshStandardMaterial from a hex colour with optional overrides. */
export function stdMat(
  hex: string,
  opts: { roughness?: number; metalness?: number; emissive?: string; emissiveIntensity?: number } = {},
): THREE.MeshStandardMaterial {
  const mat = new THREE.MeshStandardMaterial({
    color: hexColor(hex),
    roughness: opts.roughness ?? 0.92,
    metalness: opts.metalness ?? 0.03,
  });
  if (opts.emissive != null) {
    mat.emissive = hexColor(opts.emissive);
    mat.emissiveIntensity = opts.emissiveIntensity ?? 0;
  }
  return mat;
}

/** Create an emissive-glow material (for window light spill). */
export function emisMat(hex: string): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({
    color: new THREE.Color(0x0a0d0b),
    emissive: hexColor(hex),
    emissiveIntensity: 0.95,
    roughness: 1,
  });
}

// ---------------------------------------------------------------------------
// Shared structural palette (from the demo's TRAVERTINE / COLUMN / MARBLE)
// ---------------------------------------------------------------------------

export const TRAVERTINE = "#c9b596";
export const TRAVERTINE_HI = "#e0d2b2";
export const COLUMN_COLOR = "#d8c9a8";
export const TILE_COLOR = "#b5623f";
export const MARBLE = "#ddd6c8";

export const MAT_TRAVERTINE = stdMat(TRAVERTINE, { roughness: 0.95 });
export const MAT_TRAVERTINE_HI = stdMat(TRAVERTINE_HI, { roughness: 0.8 });
export const MAT_COLUMN = stdMat(COLUMN_COLOR, { roughness: 0.8 });
export const MAT_MARBLE = stdMat(MARBLE, { roughness: 0.9 });
export const MAT_GROUND = new THREE.MeshStandardMaterial({ color: 0x10160f, roughness: 1 });
export const MAT_GRID = new THREE.MeshStandardMaterial({ color: 0x10160f, roughness: 1, wireframe: true });

/** Impluvium pool material (dark reflective water). */
export const MAT_IMPLUVIUM = new THREE.MeshStandardMaterial({
  color: 0x183a4a,
  roughness: 0.15,
  metalness: 0.5,
});

/** Court floor material. */
export const MAT_COURT = stdMat("#b9a987", { roughness: 1 });

/** Soil material for grounds plots. */
export const MAT_SOIL = stdMat("#183620", { roughness: 1 });
/** Hedge material. */
export const MAT_HEDGE = stdMat("#2c5a37", { roughness: 1 });

// ---------------------------------------------------------------------------
// Wing-type material sets
// ---------------------------------------------------------------------------

export interface WingMaterials {
  /** Tinted wall: 65% travertine blended with 35% wing wall-color (Roman-palace direction). */
  wall: THREE.MeshStandardMaterial;
  roof: THREE.MeshStandardMaterial;
  light: THREE.MeshStandardMaterial;
  frieze: THREE.MeshStandardMaterial;
  roomBlock: THREE.MeshStandardMaterial;
  /** Base-course / pilaster accent strip — pure wing wall-color for identity at LOD 0. */
  pilaster: THREE.MeshStandardMaterial;
}

const _wingMatCache = new Map<string, WingMaterials>();

/**
 * Blend two hex colours: (1-t)*a + t*b.
 * Used for the travertine tint: 65% travertine / 35% wing wall-color.
 */
function blendHex(hexA: string, hexB: string, t: number): string {
  const ca = hexColor(hexA);
  const cb = hexColor(hexB);
  const r = ca.r * (1 - t) + cb.r * t;
  const g = ca.g * (1 - t) + cb.g * t;
  const b = ca.b * (1 - t) + cb.b * t;
  return "#" + new THREE.Color(r, g, b).getHexString();
}

/**
 * Get (or create) the material set for a wing type.
 * Falls back to "unknown" for unregistered types.
 *
 * Wall identity (Roman-palace direction, User-approved):
 *   - wall material: 65% travertine + 35% wing wall-color (tinted — cohesion + identity)
 *   - pilaster: pure wing wall-color (base course accent, strongest identity cue at LOD 0)
 *   - roof + frieze + window light remain the wing's own color palette
 */
export function getWingMaterials(wingType: string): WingMaterials {
  const cached = _wingMatCache.get(wingType);
  if (cached != null) return cached;

  const tok = wingTokens(wingType as WingTypeName);
  const wallHex = tok.wall;
  const roofHex = tok.roof === "none" ? tok.wall_accent : tok.roof;
  const lightHex = tok.light;

  // Tinted wall: 65% travertine / 35% wing wall-color
  const tintedWallHex = blendHex(TRAVERTINE, wallHex, 0.35);

  const mats: WingMaterials = {
    wall: stdMat(tintedWallHex, { roughness: 0.95 }),
    roof: stdMat(roofHex, { roughness: 0.82 }),
    light: emisMat(lightHex),
    frieze: stdMat(roofHex, { roughness: 0.6 }),
    roomBlock: stdMat(MARBLE, { roughness: 0.9 }),
    pilaster: stdMat(wallHex, { roughness: 0.98 }),
  };

  _wingMatCache.set(wingType, mats);
  return mats;
}

// ---------------------------------------------------------------------------
// Palace materials (great hall + portico)
// ---------------------------------------------------------------------------

const _palTok = DESIGN_TOKENS.buildings.nook_palace;

export const MAT_PALACE_WALL = stdMat(TRAVERTINE, { roughness: 0.95 });
export const MAT_PALACE_ROOF = stdMat(_palTok.roof ?? "#9c6a4a", { roughness: 0.82 });
export const MAT_PALACE_ACCENT = stdMat(_palTok.accent ?? "#9be7c2", { roughness: 0.4, metalness: 0.3 });
export const MAT_PALACE_CORNICE = stdMat(TRAVERTINE_HI, { roughness: 0.8 });
export const MAT_PALACE_BASE = stdMat("#9a8a6e", { roughness: 1 });
export const MAT_PEDIMENT = stdMat(MARBLE, { roughness: 0.85 });

// ---------------------------------------------------------------------------
// Workshop materials (fabrica)
// ---------------------------------------------------------------------------

const _wsTok = DESIGN_TOKENS.buildings.workshop;

export const MAT_WORKSHOP_WALL = stdMat(_wsTok.wall ?? "#b8a878", { roughness: 0.95 });
export const MAT_WORKSHOP_ROOF = stdMat(_wsTok.roof ?? "#6a5320", { roughness: 0.82 });
export const MAT_WORKSHOP_ACCENT = stdMat(_wsTok.accent ?? "#caa15f", { roughness: 0.4, metalness: 0.4 });
export const MAT_WORKSHOP_LIGHT = emisMat(_wsTok.accent ?? "#ffd98a");

// ---------------------------------------------------------------------------
// Ruin material (unknown / deleted)
// ---------------------------------------------------------------------------

const _unknownTok = wingTokens("unknown");
export const MAT_RUIN_WALL = stdMat(_unknownTok.wall, { roughness: 0.95 });
export const MAT_RUIN_ACCENT = stdMat(_unknownTok.wall_accent, { roughness: 0.95 });
