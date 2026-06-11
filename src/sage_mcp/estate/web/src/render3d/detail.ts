/**
 * Detail panel content for the 3D renderer.
 *
 * Builds right-panel HTML from a SceneGraph node id.
 * Reads ONLY the SceneGraph — never the EstateModel (ADR-0001).
 *
 * Leaf-type vocabulary (design tokens zoom.leaf_distinction_note):
 * - Nook room leaf = "drawer" (verbatim memory unit)
 * - Workshop agent leaf = "agent.md file"
 * - Tablinum leaf = "config setting"
 * Never flatten all leaves to "drawer".
 *
 * WHERE: src/sage_mcp/estate/web/src/render3d/detail.ts
 */

import type { PlacedKind, PlacedNode, RuinNode, SceneGraph } from "../model/types";
import type { DesignTokens } from "../render/tokens";

// ---------------------------------------------------------------------------
// Detail content shape (mirrors superseded detailPane.ts contract)
// ---------------------------------------------------------------------------

export interface DetailStat {
  label: string;
  value: string | number;
}

export interface DetailContent {
  nodeId: string;
  title: string;
  kindLabel: string;
  stats: DetailStat[];
  description: string;
  leafType: "drawer" | "agent.md" | "config setting" | null;
  flyTarget: null; // 3D renderer handles fly via OrbitControls directly
}

export interface RuinDetailContent {
  deletedId: string;
  slot: number;
  title: string;
  description: string;
}

// ---------------------------------------------------------------------------
// Kind labels
// ---------------------------------------------------------------------------

const KIND_LABELS: Record<PlacedKind, string> = {
  palace: "Memory Palace (Nook)",
  wing: "Wing",
  room: "Room",
  "drawer-glyph": "Drawer Group",
  workshop: "Workshop (Fabrica)",
  "agent-room": "Agent Room",
  "family-wing": "Agent Family",
  grounds: "Grounds (Peristylium)",
  plot: "Repository Plot",
};

const WING_TYPE_DESC: Record<string, string> = {
  dev: "Development wing — code, decisions, audits, plans.",
  project: "Project wing — lived-in working space for active projects.",
  knowledge: "Knowledge wing — the library; research, facts, and references.",
  ops: "Operations wing — infrastructure, telemetry, and automation.",
  meta: "Meta wing — framework reflection; the estate reasoning about itself.",
  unknown: "Unregistered wing type — roofless ruin. Add to wing_config.json to classify.",
  personal: "Personal wing — user-specific context and memory.",
};

// ---------------------------------------------------------------------------
// Detail content builder
// ---------------------------------------------------------------------------

export function buildDetailContent(
  nodeId: string,
  scene: SceneGraph,
  _tokens: DesignTokens,
): DetailContent | null {
  const node = scene.placed[nodeId];
  if (node == null) return null;

  const kindLabel = KIND_LABELS[node.kind] ?? node.kind;
  const stats = buildStats(node);
  const { description, leafType } = buildDescriptionAndLeaf(node);

  return { nodeId, title: node.label, kindLabel, stats, description, leafType, flyTarget: null };
}

export function buildRuinDetail(
  deletedId: string,
  scene: SceneGraph,
): RuinDetailContent | null {
  const ruin: RuinNode | undefined = scene.ruins.find((r) => r.deletedId === deletedId);
  if (ruin == null) return null;
  return {
    deletedId,
    slot: ruin.slot,
    title: ruin.label ?? deletedId,
    description: `This slot (${ruin.slot}) belonged to "${deletedId}" which has been deleted. The slot is permanently reserved — it will never be assigned to a new id (ADR-0005). The ruin marks where the node used to stand.`,
  };
}

function buildStats(node: PlacedNode): DetailStat[] {
  const stats: DetailStat[] = [];
  stats.push({ label: "Slot", value: node.slot });
  if (node.wingType != null) stats.push({ label: "Wing type", value: node.wingType });
  if (node.roomCount != null) stats.push({ label: "Rooms", value: node.roomCount });
  if (node.drawerCount != null) stats.push({ label: "Drawers", value: node.drawerCount });
  if (node.drawerBucket != null && node.drawerBucket > 0) {
    stats.push({ label: "Drawer density (bucket)", value: `${node.drawerBucket}/5` });
  }
  if (node.dirty === true) {
    stats.push({ label: "Status", value: "dirty (uncommitted changes)" });
  } else if (node.dirty === false) {
    stats.push({ label: "Status", value: "clean" });
  }
  stats.push({ label: "Parent", value: node.parentId ?? "—" });
  return stats;
}

function buildDescriptionAndLeaf(node: PlacedNode): {
  description: string;
  leafType: DetailContent["leafType"];
} {
  switch (node.kind) {
    case "palace":
      return { description: "The Memory Palace — home of all Nook drawers, organised into wings by project and type.", leafType: null };
    case "wing":
      return { description: WING_TYPE_DESC[node.wingType ?? "unknown"] ?? "Wing of unknown type.", leafType: null };
    case "room":
      return { description: "A room groups drawers by session or topic. Drawer density drives interior light.", leafType: "drawer" };
    case "drawer-glyph":
      return { description: "A bucketed drawer-count glyph. Each glyph represents up to 10 drawers.", leafType: "drawer" };
    case "workshop":
      return { description: "The Workshop (Fabrica) — all agents live here, organised by family. Gold forge-light, one lit room per active agent.", leafType: null };
    case "family-wing":
      return { description: `Agent family wing. All agents in the "${node.label}" family are grouped here for spatial stability (ADR-0005).`, leafType: null };
    case "agent-room":
      return { description: "An agent's room inside the Workshop. The leaf at Level 3 (Phase 4) is the agent.md file.", leafType: "agent.md" };
    case "grounds":
      return { description: "The Grounds (Peristylium) — open courtyard. Each repo has a plot here. Dirty repos show scaffolding.", leafType: null };
    case "plot":
      return {
        description: node.dirty === true
          ? "This repository has uncommitted changes (dirty). Scaffolding and sawhorses mark the construction zone."
          : "A repository plot in the grounds. Clean and stable.",
        leafType: null,
      };
  }
}

// ---------------------------------------------------------------------------
// HTML rendering helpers
// ---------------------------------------------------------------------------

function escHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

export function renderDetailPaneHtml(
  content: DetailContent,
  tokens: DesignTokens,
): string {
  const pane = tokens.detail_pane;
  // Resolve role-name token references through typography (body_font = "label", data_font = "data")
  const bodyFamily = tokens.typography.label.family;
  const dataFamily = tokens.typography.data.family;
  const titleFamily = tokens.typography.nameplate.family;
  const statsRows = content.stats
    .map((s) => `<tr>
      <td style="color:${tokens.palette.ink.muted};font-family:${bodyFamily};padding:2px 8px 2px 0;">${escHtml(String(s.label))}</td>
      <td style="color:${tokens.palette.ink.primary};font-family:${dataFamily};">${escHtml(String(s.value))}</td>
    </tr>`)
    .join("\n");
  const leafBadge = content.leafType != null
    ? `<span style="display:inline-block;background:#ffffff11;border:1px solid ${tokens.global.line};border-radius:3px;padding:1px 6px;font-size:10px;font-family:${dataFamily};color:${tokens.palette.ink.muted};margin-left:4px;">${escHtml(content.leafType)}</span>`
    : "";
  return `<div id="detail-pane" style="background:${pane.background};border-left:1px solid ${pane.border};padding:20px;min-width:260px;max-width:320px;overflow-y:auto;">
  <h2 style="font-family:${titleFamily};color:${tokens.palette.ink.primary};font-size:18px;margin:0 0 4px 0;letter-spacing:0.08em;text-transform:uppercase;">${escHtml(content.title)}${leafBadge}</h2>
  <p style="font-family:${bodyFamily};color:${tokens.palette.ink.muted};font-size:${tokens.typography.scale_px.sm}px;margin:0 0 16px 0;">${escHtml(content.kindLabel)}</p>
  <table style="width:100%;border-collapse:collapse;font-size:12px;">${statsRows}</table>
  <p style="font-family:${bodyFamily};color:${tokens.palette.ink.secondary};font-size:12px;margin-top:16px;line-height:1.5;">${escHtml(content.description)}</p>
</div>`;
}

export function renderEmptyDetailPaneHtml(tokens: DesignTokens): string {
  const pane = tokens.detail_pane;
  return `<div id="detail-pane" style="background:${pane.background};border-left:1px solid ${pane.border};padding:20px;min-width:260px;max-width:320px;display:flex;align-items:center;justify-content:center;">
  <p style="font-family:${tokens.typography.label.family};color:${tokens.palette.ink.muted};font-size:12px;text-align:center;">Click a building, wing, or plot<br>to inspect it here.</p>
</div>`;
}

// ---------------------------------------------------------------------------
// Panel content builders
// ---------------------------------------------------------------------------

export function renderDetailPanel(
  nodeId: string | null,
  scene: SceneGraph,
  tokens: DesignTokens,
): string {
  if (nodeId == null) return renderEmptyDetailPaneHtml(tokens);
  const content = buildDetailContent(nodeId, scene, tokens);
  if (content == null) return renderEmptyDetailPaneHtml(tokens);
  return renderDetailPaneHtml(content, tokens);
}

// ---------------------------------------------------------------------------
// Building overview panel (cockpit estate-overview state)
// ---------------------------------------------------------------------------

export function renderOverviewPanel(
  scene: SceneGraph,
  tokens: DesignTokens,
  selectedId: string | null,
  level: number,
): string {
  const esc = escHtml;

  // Wing rows from scene graph
  const wingNodes = Object.entries(scene.placed).filter(([, n]) => n.kind === "wing");
  const wingRows = wingNodes.map(([, node]) => {
    const wt = tokens.wing_types[node.wingType as keyof typeof tokens.wing_types];
    const roofHex = (wt != null && wt.roof !== "none") ? wt.roof : "#33383b";
    const rc = node.roomCount ?? 0;
    const dc = node.drawerCount ?? 0;
    return `<div class="wingrow">
      <span class="swatch" style="background:${esc(roofHex)}"></span>
      <span class="wn">${esc(node.label)}</span>
      <span class="wc">${rc} rm &middot; ${dc} dr</span>
    </div>`;
  }).join("");

  const totalDrawers = wingNodes.reduce((a, [, n]) => a + (n.drawerCount ?? 0), 0);
  const totalRooms = wingNodes.reduce((a, [, n]) => a + (n.roomCount ?? 0), 0);

  const nookCard = buildingCard("nook", "The Nook", "Domus", "memory is identity", "&#10022;", "#10362a", "#9be7c2", selectedId === "nook");
  const wsCard = buildingCard("workshop", "The Workshop", "Fabrica", "where the agents work", "&#9954;", "#3a3018", "#ffd98a", selectedId === "workshop");
  const grCard = buildingCard("grounds", "The Grounds", "Peristylium", `${wingNodes.length} wing plots`, "&#10045;", "#13361f", "#7bd58f", selectedId === "grounds");

  if (selectedId === "nook") {
    return `<div class="panel-pad">
      <div><div class="p-title">The Nook</div><div class="p-sub">Domus &middot; memory is identity</div></div>
      ${nookCard}
      <div class="legend">
        <div class="legend-h">Wings <div class="spacer"></div>${wingNodes.length}</div>
        ${wingRows}
      </div>
      <div class="legend">
        <div class="legend-h">Store</div>
        <div style="padding:10px 13px">
          <div class="kv"><span class="k">drawers</span><span class="v mono">${totalDrawers}</span></div>
          <div class="kv"><span class="k">rooms</span><span class="v mono">${totalRooms}</span></div>
          <div class="kv"><span class="k">wing-types</span><span class="v mono">6 + unknown</span></div>
        </div>
      </div>
    </div>`;
  }

  if (selectedId === "workshop") {
    const agentNodes = Object.entries(scene.placed).filter(([, n]) => n.kind === "agent-room");
    const famNodes = Object.entries(scene.placed).filter(([, n]) => n.kind === "family-wing");
    return `<div class="panel-pad">
      <div><div class="p-title">The Workshop</div><div class="p-sub">Fabrica &middot; where the agents work</div></div>
      ${wsCard}
      <div class="legend">
        <div class="legend-h">Roster</div>
        <div style="padding:10px 13px">
          <div class="kv"><span class="k">agents</span><span class="v mono">${agentNodes.length}</span></div>
          <div class="kv"><span class="k">families</span><span class="v mono">${famNodes.length}</span></div>
          <div class="kv"><span class="k">rev</span><span class="v mono">${scene.meta.revision}</span></div>
        </div>
      </div>
    </div>`;
  }

  if (selectedId === "grounds") {
    const plots = Object.entries(scene.placed).filter(([, n]) => n.kind === "plot");
    const plotRows = plots.map(([, p]) => {
      const isDirty = p.dirty === true;
      return `<div class="legend-row">
        <span class="legend-n" style="color:${isDirty ? "var(--warn)" : "var(--ok)"}">&#9638;</span>
        <div class="legend-t"><b>${esc(p.label)}</b><span>${isDirty ? "uncommitted (scaffold up)" : "clean"}</span></div>
      </div>`;
    }).join("");
    return `<div class="panel-pad">
      <div><div class="p-title">The Grounds</div><div class="p-sub">Peristylium &middot; the courtyard</div></div>
      ${grCard}
      <div class="legend">
        <div class="legend-h">Repo plots</div>
        ${plotRows}
      </div>
    </div>`;
  }

  // Default: estate overview with semantic zoom legend
  const LEVEL_NAMES = ["Property", "Wings & rooms", "Closer · rooms", "Closest · drawers"];
  const LEVEL_DESC = [
    "all buildings · roofs on",
    "roofs lift · wing names",
    "rooms inside each wing",
    "drawer cabinets",
  ];
  const lodRows = LEVEL_NAMES.map((name, i) =>
    `<div class="legend-row ${i === level ? "on" : ""}">
      <span class="legend-n">${i}</span>
      <div class="legend-t"><b>${esc(name)}</b><span>${esc(LEVEL_DESC[i] ?? "")}</span></div>
    </div>`,
  ).join("");

  return `<div class="panel-pad">
    <div><div class="p-title">The Sage Property</div><div class="p-sub">A sealed Roman estate. Fly closer to descend.</div></div>
    ${nookCard}${wsCard}${grCard}
    <div class="legend">
      <div class="legend-h">Semantic zoom</div>
      ${lodRows}
    </div>
  </div>`;
}

function buildingCard(
  id: string,
  title: string,
  roman: string,
  sub: string,
  icon: string,
  iconBg: string,
  iconColor: string,
  selected: boolean,
): string {
  return `<div class="estcard ${selected ? "sel" : ""}" data-id="${escHtml(id)}">
    <div class="ec-ico" style="background:${escHtml(iconBg)};color:${escHtml(iconColor)}">${escHtml(icon)}</div>
    <div><div class="ec-t">${escHtml(title)}</div><div class="ec-s">${escHtml(roman)} &middot; ${escHtml(sub)}</div></div>
    <div class="ec-arrow">&#8250;</div>
  </div>`;
}
