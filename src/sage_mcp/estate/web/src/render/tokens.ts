/**
 * Design token types + loader for the Nocturne Villa visual grammar.
 *
 * Single source: src/design/estate-design-tokens.json (copied from
 * docs/projects/sage-estate-dashboard/estate-design-tokens.json at build time).
 * The renderer imports this module; nothing else reads the JSON directly.
 *
 * Architecture rule (plan §7 / Phase 2b): the renderer consumes only the
 * SceneGraph from the layout engine — it MUST NOT read EstateModel directly.
 * This file provides the second input to renderScene(sceneGraph, tokens, opts).
 *
 * WHERE: src/sage_mcp/estate/web/src/render/tokens.ts
 */

import rawTokens from "../design/estate-design-tokens.json" assert { type: "json" };

// ---------------------------------------------------------------------------
// Wing-type token shape — one entry per WingType value
// ---------------------------------------------------------------------------

export interface WingTypeTokens {
  label: string;
  wall: string;
  wall_accent: string;
  roof: string | "none";
  roof_shape: string;
  light: string;
  motif: string;
  rationale: string;
}

export type WingTypeName = "dev" | "project" | "knowledge" | "ops" | "meta" | "personal" | "unknown";

export type WingTypeMap = Record<WingTypeName, WingTypeTokens>;

// ---------------------------------------------------------------------------
// Building token shape
// ---------------------------------------------------------------------------

export interface BuildingTokens {
  label: string;
  wall?: string;
  wall_accent?: string;
  roof?: string;
  roof_shape?: string;
  accent?: string;
  grandeur?: string;
  ground?: string;
  plot?: string;
  plot_edge?: string;
  motif?: string;
  rationale?: string;
}

export type BuildingName =
  | "nook_palace"
  | "workshop"
  | "grounds"
  | "horrea"
  | "tablinum"
  | "gate";

export type BuildingMap = Record<BuildingName, BuildingTokens>;

// ---------------------------------------------------------------------------
// Iso projection tokens
// ---------------------------------------------------------------------------

export interface IsoTokens {
  projection: string;
  tile_ratio: number;
  elevation_unit_px: number;
  wall_height_units: number;
  shadow: {
    color: string;
    blur_px: number;
    offset_units: number;
  };
}

// ---------------------------------------------------------------------------
// Background tokens
// ---------------------------------------------------------------------------

export interface BackgroundTokens {
  base: string;
  atmosphere: string;
  grain: { opacity: number; blend: string; note: string };
  ground_grid: { line: string; cell_px: number; note: string };
}

// ---------------------------------------------------------------------------
// Typography
// ---------------------------------------------------------------------------

export interface TypographyScale {
  xs: number;
  sm: number;
  base: number;
  lg: number;
  xl: number;
  display: number;
}

export interface TypographyTokens {
  nameplate: { family: string; use: string; letter_spacing_em: number; case: string };
  label: { family: string; use: string };
  data: { family: string; use: string };
  scale_px: TypographyScale;
}

// ---------------------------------------------------------------------------
// Palette
// ---------------------------------------------------------------------------

export interface PaletteTokens {
  ink: { primary: string; secondary: string; muted: string };
  accent: { terracotta: string; gold: string; verdigris: string };
  signal: {
    ok: string;
    info: string;
    violet: string;
    danger: string;
    warn: string;
  };
}

// ---------------------------------------------------------------------------
// State signals
// ---------------------------------------------------------------------------

export interface StateSignalEntry {
  trigger: string;
  visual: string;
  source: string;
}

export type StateSignals = Record<string, StateSignalEntry>;

// ---------------------------------------------------------------------------
// Zoom tokens
// ---------------------------------------------------------------------------

export interface ZoomLevel {
  level: number;
  name: string;
  shows: string;
  lod_min_scale: number;
}

export interface ZoomTransitions {
  roof_lift: { duration_ms: number; easing: string; note: string };
  camera_fly: { duration_ms: number; easing: string; note: string };
  stagger_ms: number;
}

export interface ZoomTokens {
  _note: string;
  levels: ZoomLevel[];
  leaf_distinction_note: string;
  transitions: ZoomTransitions;
}

// ---------------------------------------------------------------------------
// Detail pane
// ---------------------------------------------------------------------------

export interface DetailPaneTokens {
  position: string;
  background: string;
  border: string;
  mirrors: string;
  title_font: string;
  body_font: string;
  data_font: string;
}

// ---------------------------------------------------------------------------
// Full DesignTokens type
// ---------------------------------------------------------------------------

export interface DesignTokens {
  $schemaNote?: string;
  meta: {
    name: string;
    version: string;
    concept: string;
    direction: string;
    status: string;
  };
  global: {
    background: BackgroundTokens;
    iso: IsoTokens;
    panel: string;
    line: string;
    selection: { ring: string; ring_width_px: number; halo: string };
  };
  typography: TypographyTokens;
  palette: PaletteTokens;
  wing_types: WingTypeMap;
  buildings: BuildingMap;
  state_signals: StateSignals;
  zoom: ZoomTokens;
  detail_pane: DetailPaneTokens;
  accessibility: {
    min_contrast: string;
    non_color_state: string;
    motion: string;
  };
  // Layout-engine pass-through keys (not used by renderer but typed for compatibility)
  baseCell?: number;
  drawerPageSize?: number;
  roomsPerRow?: number;
  agentsPerRow?: number;
  plotsPerRow?: number;
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// Loaded singleton — import this in the renderer
// ---------------------------------------------------------------------------

/**
 * The design tokens loaded from the single-source JSON.
 * Cast is safe: the JSON shape is controlled by the estate-design-maintainer
 * ADR and reviewed alongside this type definition.
 */
export const DESIGN_TOKENS: DesignTokens = rawTokens as unknown as DesignTokens;

// ---------------------------------------------------------------------------
// Load-time validation — fail loud if the token file is missing required keys
// ---------------------------------------------------------------------------

/** All wing type keys that must be present in estate-design-tokens.json. */
const REQUIRED_WING_TYPE_KEYS: readonly WingTypeName[] = [
  "dev", "project", "knowledge", "ops", "meta", "personal", "unknown",
];

/** Required string sub-fields on each WingTypeTokens block. */
const REQUIRED_WING_TYPE_FIELDS: ReadonlyArray<keyof WingTypeTokens> = [
  "label", "wall", "wall_accent", "roof", "roof_shape", "light", "motif",
];

(function validateTokens(): void {
  const wt = rawTokens.wing_types as Record<string, unknown>;
  for (const key of REQUIRED_WING_TYPE_KEYS) {
    const block = wt[key];
    if (block == null || typeof block !== "object") {
      throw new Error(`estate-design-tokens.json: missing wing_types.${key}`);
    }
    for (const field of REQUIRED_WING_TYPE_FIELDS) {
      if (typeof (block as Record<string, unknown>)[field] !== "string") {
        throw new Error(
          `estate-design-tokens.json: wing_types.${key}.${field} must be a string`,
        );
      }
    }
  }
})();

/**
 * Resolve a wing-type's token block, falling back to `unknown` for any
 * unregistered type. This is the ONLY lookup path for wing type tokens in the
 * renderer — never branch on raw string equality.
 */
export function wingTokens(type: string): WingTypeTokens {
  const registered = DESIGN_TOKENS.wing_types as Record<string, WingTypeTokens | undefined>;
  const fb = registered[type] ?? registered["unknown"];
  if (fb == null) throw new Error("wing_types.unknown token block missing");
  return fb;
}

/**
 * Resolve a building's token block by name, falling back to an empty label
 * if the key is somehow missing (mirrors wingTokens safety pattern).
 */
export function buildingTokens(name: BuildingName): BuildingTokens {
  const registered = DESIGN_TOKENS.buildings as Record<string, BuildingTokens | undefined>;
  return registered[name] ?? { label: name };
}
