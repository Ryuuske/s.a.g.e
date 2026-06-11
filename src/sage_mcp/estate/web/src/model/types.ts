/**
 * TypeScript type stubs mirroring the top-level shape of estate-model.schema.json.
 *
 * These are SCAFFOLD interfaces — Phase 0 only. Full field coverage is a Phase 2a task.
 * The authoritative contract is the JSON Schema at:
 *   docs/projects/sage-estate-dashboard/estate-model.schema.json
 */

/** Top-level estate snapshot. Mirrors estate-model.schema.json root object. */
export interface EstateModel {
  version: "1.0";
  revision: number;
  captured_at: string;
  property: Property;
  buildings: Building[];
  grounds: Grounds;
  outbuildings: Outbuildings;
}

/** Property-level metadata and health. */
export interface Property {
  name: string;
  /** Required by estate-model.schema.json property.required. */
  isolation: {
    windows_mounts?: boolean;
    interop?: boolean;
    systemd?: boolean;
  };
  health: {
    governance: Record<string, unknown>;
    store: Record<string, unknown>;
  };
}

/** A building — either the Nook palace or the Workshop. */
export type Building = NookBuilding | WorkshopBuilding;

export interface NookBuilding {
  id: "nook";
  kind: "palace";
  title: string;
  wings: Wing[];
  tunnels: Tunnel[];
  closets: { consolidated?: number; decayed?: number };
  kg: { entities?: number; relations?: number };
  diaries?: DiaryEntry[];
}

export interface WorkshopBuilding {
  id: "workshop";
  kind: "workshop";
  title: string;
  agents: Agent[];
  armory: {
    skills?: number;
    rules?: number;
    hooks?: number;
    tools?: number;
  };
}

/** A wing groups rooms by project/topic, belonging to the Nook palace. */
export interface Wing {
  id: string;
  type: WingType;
  title: string;
  slot: number;
  bucket?: Bucket;
  rooms: Room[];
  hall_counts: Record<string, number>;
  drawer_total: number;
}

/** Registered wing types, synced with wing_config.json plus the unknown catch-all. */
export type WingType =
  | "dev"
  | "project"
  | "knowledge"
  | "ops"
  | "meta"
  | "personal"
  | "unknown";

/** A room groups drawers by session/topic within a wing. */
export interface Room {
  id: string;
  title: string;
  slot: number;
  drawer_count: number;
  size_bytes?: number;
  drawers?: Drawer[];
}

/** Drawer metadata — no body/content field (contract invariant). */
export interface Drawer {
  id: string;
  hall: string;
  slot: number;
  agent?: string;
  size_bytes?: number;
  captured_at?: string;
  strength?: number;
}

/** Undirected cross-wing tunnel link. */
export interface Tunnel {
  id: string;
  name?: string;
  endpoints: [string, string];
}

/** A Workshop agent. */
export interface Agent {
  id: string;
  family: string;
  model?: string;
  tools?: string[];
  description?: string;
  slot: number;
  bucket?: Bucket;
}

/** Slot-stable grouping bucket for large rosters. */
export interface Bucket {
  key: string;
  slot: number;
  page_size?: number;
}

/** Grounds (Peristylium) — working repo plots. */
export interface Grounds {
  plots: Plot[];
}

/** A repo plot in the grounds. */
export interface Plot {
  id: string;
  title: string;
  slot: number;
  path?: string;
  files?: number;
  dirty?: boolean;
  memory_wing?: string;
}

/** Outbuildings: Horrea, Tablinum, Gate. */
export interface Outbuildings {
  horrea: Horrea;
  tablinum: Tablinum;
  gate: Gate;
}

export interface Horrea {
  snapshots?: Array<{ id: string; taken_at?: string; reason?: string }>;
}

export interface Tablinum {
  config?: {
    permission_mode?: string;
    windows_mounts?: boolean;
    interop?: boolean;
    systemd?: boolean;
  };
}

export interface Gate {
  danger_actions?: string[];
}

/** A per-agent diary count entry. */
export interface DiaryEntry {
  agent: string;
  entries: number;
}

// ---------------------------------------------------------------------------
// Scene graph — render-agnostic placement output of the layout engine
// ---------------------------------------------------------------------------

/**
 * The full render-agnostic scene graph emitted by `layout()`.
 *
 * - `placed` maps every active node id to its placed footprint.
 * - `ruins` lists the gap entries for deleted ids (slot + last-known position).
 * - `meta` carries ledger + model provenance so the renderer can display state.
 */
export interface SceneGraph {
  /** Active placed nodes, keyed by stable node id. */
  placed: Record<string, PlacedNode>;
  /**
   * Gap entries for ids that were deleted after first-sight.
   * Each ruin records the slot and position so the renderer can draw the
   * empty-closet glyph without needing a live id.
   */
  ruins: RuinNode[];
  /** Provenance carried through for debugging / telemetry. */
  meta: SceneGraphMeta;
}

/** Iso-coordinate footprint of a placed node. */
export interface PlacedNode {
  /** Stable slot number from the ledger — never changes after first-sight. */
  slot: number;
  /**
   * Iso X coordinate (column in the isometric grid).
   * Unit: abstract grid cells; the renderer maps to pixels via design tokens.
   */
  isoX: number;
  /**
   * Iso Y coordinate (row in the isometric grid).
   * Unit: abstract grid cells.
   */
  isoY: number;
  /** Width in grid cells. */
  w: number;
  /** Height in grid cells. */
  h: number;
  /** Semantic kind tag — drives renderer tile selection. */
  kind: PlacedKind;
  /** Human-readable label (title from the model). */
  label: string;
  /** Optional parent id (wing owns rooms; room owns drawer-glyphs). */
  parentId?: string;
  /** Bucketed drawer-glyph count for rooms (0 when not applicable). */
  drawerBucket?: number;
  /** Wing type for palace wings (undefined for non-wing nodes). */
  wingType?: WingType;
  /** Whether this plot is dirty (grounds only). */
  dirty?: boolean;
  /**
   * Room count display field for wing nodes (populated by layout.ts).
   * Renderer uses this for wing labels without touching EstateModel.
   */
  roomCount?: number;
  /**
   * Drawer count display field for wing nodes (populated by layout.ts).
   * Renderer uses this for wing labels without touching EstateModel.
   */
  drawerCount?: number;
}

/**
 * Semantic kind tags — closed enum so the renderer's tile-map is exhaustive.
 * A future variant (Three.js mesh) uses the same tags.
 */
export type PlacedKind =
  | "palace"        // The Nook building as a whole
  | "wing"          // A wing block inside the palace
  | "room"          // A room cell inside a wing
  | "drawer-glyph"  // Bucketed drawer-count glyph inside a room
  | "workshop"      // The Workshop building as a whole
  | "agent-room"    // One agent's room inside the workshop
  | "family-wing"   // A family-bucketed wing block inside the workshop
  | "grounds"       // The Grounds area as a whole
  | "plot";         // A repo plot in the grounds

/** A gap entry for a deleted id — the renderer draws the empty-closet glyph here. */
export interface RuinNode {
  /** The slot that was assigned to the deleted id. */
  slot: number;
  /**
   * Last-known iso coordinates (so the renderer can place the glyph exactly
   * where the node used to sit — spatial memory is preserved as "decayed").
   */
  isoX: number;
  isoY: number;
  /** Width + height inherited from the node's original footprint. */
  w: number;
  h: number;
  /** Kind of the deleted node (so renderer picks the right ruin glyph). */
  kind: PlacedKind;
  /** Optional label from the deleted node (may be empty for purged ids). */
  label?: string;
  /** The stable id of the deleted node (kept for cross-referencing the ledger). */
  deletedId: string;
}

/** Provenance block attached to every scene graph. */
export interface SceneGraphMeta {
  /** Model revision this graph was computed from. */
  revision: number;
  /** ISO timestamp from the model's `captured_at`. */
  capturedAt: string;
  /** Total slots assigned in the ledger (active + ruins). */
  totalSlots: number;
  /** Number of ruin (gap) entries. */
  ruinCount: number;
}

// ---------------------------------------------------------------------------
// Design tokens — consumed by the renderer (Phase 2b fills these out fully)
// ---------------------------------------------------------------------------

/**
 * Design tokens consumed by the renderer.
 * Phase 2b defines the full visual contract; this shape is the minimal
 * contract the layout engine needs to compute footprint sizes.
 */
export interface DesignTokens {
  /**
   * Base grid-cell size in abstract units.
   * The layout engine uses this to compute relative sizes; the renderer
   * multiplies by its pixel-per-unit scale factor.
   * @default 1
   */
  baseCell?: number;
  /**
   * Number of drawers that fit in one drawer-glyph bucket.
   * `drawerBucket = floor(drawerCount / drawerPageSize)`.
   * @default 10
   */
  drawerPageSize?: number;
  /**
   * Maximum rooms per wing row before wrapping to a new row.
   * @default 4
   */
  roomsPerRow?: number;
  /**
   * Maximum agent-rooms per family-wing row.
   * @default 4
   */
  agentsPerRow?: number;
  /**
   * Maximum plots per grounds row.
   * @default 4
   */
  plotsPerRow?: number;
  /** Pass-through bucket for renderer-only tokens (layout engine ignores). */
  [key: string]: unknown;
}
