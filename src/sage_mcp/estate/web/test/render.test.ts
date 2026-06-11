// @vitest-environment happy-dom
/**
 * Render token tests — Phase 2c.
 *
 * The 2.5D SVG renderer (render.ts, camera.ts, detailPane.ts) was superseded
 * by the Three.js 3D renderer (src/render3d/) per ADR-0001. SVG-specific tests
 * have been removed; 3D scene tests live in test/scene3d.test.ts.
 *
 * This file retains:
 * - Tokens module tests (DESIGN_TOKENS, wingTokens, buildingTokens)
 * - Token drift guard (canonical and web copies must stay deep-equal)
 * - Layout fixture sanity (scene graph produces placed nodes)
 *
 * WHERE: src/sage_mcp/estate/web/test/render.test.ts
 */

import { describe, expect, it } from "vitest";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";
import type { EstateModel, SceneGraph } from "../src/model/types";
import { layout } from "../src/layout/layout";
import { DESIGN_TOKENS, wingTokens, buildingTokens } from "../src/render/tokens";

// ---------------------------------------------------------------------------
// Fixture
// ---------------------------------------------------------------------------

import sampleRaw from "../../../../../tests/estate/fixtures/estate-model.sample.json" assert { type: "json" };
const sample = sampleRaw as EstateModel;

const tokens = DESIGN_TOKENS;
const scene: SceneGraph = layout(sample, tokens);

// ---------------------------------------------------------------------------
// Tokens module
// ---------------------------------------------------------------------------

describe("tokens module", () => {
  it("DESIGN_TOKENS is loaded and has the correct meta name", () => {
    expect(tokens.meta.name).toBe("Sage Estate — Nocturne Villa");
  });

  it("wingTokens('dev') returns dev material", () => {
    const wt = wingTokens("dev");
    expect(wt.wall).toBe("#2b3640");
    expect(wt.roof).toBe("#1d2730");
  });

  it("wingTokens('unknown') returns ruin material", () => {
    const wt = wingTokens("unknown");
    expect(wt.roof).toBe("none");
    expect(wt.wall).toBe("#33383b");
  });

  it("wingTokens('anything-unregistered') falls back to unknown", () => {
    const wt = wingTokens("does-not-exist");
    expect(wt.wall).toBe(wingTokens("unknown").wall);
  });

  it("all 5 registered wing types are present in tokens", () => {
    for (const t of ["dev", "project", "knowledge", "ops", "meta"] as const) {
      const wt = wingTokens(t);
      expect(wt.wall).toBeTruthy();
      expect(wt.light).toBeTruthy();
    }
  });

  it("wingTokens('personal') returns warm plaster (#7c4a38)", () => {
    const wt = wingTokens("personal");
    expect(wt.wall).toBe("#7c4a38");
    expect(wt.roof).not.toBe("none");
  });

  it("zoom levels array has 4 entries (0–3)", () => {
    expect(tokens.zoom.levels).toHaveLength(4);
    expect(tokens.zoom.levels[0]!.level).toBe(0);
    expect(tokens.zoom.levels[3]!.level).toBe(3);
  });

  it("roof_lift transition tokens are present", () => {
    expect(tokens.zoom.transitions.roof_lift.duration_ms).toBe(460);
    expect(tokens.zoom.transitions.roof_lift.easing).toContain("cubic-bezier");
  });

  it("buildingTokens('nook_palace') returns palace tokens", () => {
    const bt = buildingTokens("nook_palace");
    expect(bt.label).toBeTruthy();
    expect(bt.roof).toBeTruthy();
  });

  it("buildingTokens('workshop') returns workshop tokens", () => {
    const bt = buildingTokens("workshop");
    expect(bt.wall).toBeTruthy();
    expect(bt.accent).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Token drift guard
// ---------------------------------------------------------------------------

describe("token drift guard: canonical and web token files are deep-equal", () => {
  const __filename = fileURLToPath(import.meta.url);
  const __dirname = path.dirname(__filename);

  it("docs/projects/.../estate-design-tokens.json deep-equals src/design/estate-design-tokens.json", () => {
    const repoRoot = path.resolve(__dirname, "../../../../../");
    const canonicalPath = path.join(
      repoRoot,
      "docs/projects/sage-estate-dashboard/estate-design-tokens.json",
    );
    const webPath = path.join(
      repoRoot,
      "src/sage_mcp/estate/web/src/design/estate-design-tokens.json",
    );
    const canonical = JSON.parse(fs.readFileSync(canonicalPath, "utf-8")) as unknown;
    const web = JSON.parse(fs.readFileSync(webPath, "utf-8")) as unknown;
    expect(canonical).toEqual(web);
  });
});

// ---------------------------------------------------------------------------
// Layout fixture sanity
// ---------------------------------------------------------------------------

describe("scene graph sanity (from fixture)", () => {
  it("placed contains nook palace", () => {
    expect(scene.placed["nook"]).toBeDefined();
    expect(scene.placed["nook"]!.kind).toBe("palace");
  });

  it("placed contains workshop", () => {
    expect(scene.placed["workshop"]).toBeDefined();
    expect(scene.placed["workshop"]!.kind).toBe("workshop");
  });

  it("placed contains grounds", () => {
    expect(scene.placed["grounds"]).toBeDefined();
    expect(scene.placed["grounds"]!.kind).toBe("grounds");
  });

  it("wing nodes have roomCount and drawerCount populated (ADR-0001 enrichment)", () => {
    const devWing = scene.placed["wing:dev:sage"];
    expect(devWing).toBeDefined();
    expect(devWing!.roomCount).toBeDefined();
    expect(typeof devWing!.roomCount).toBe("number");
    expect(devWing!.drawerCount).toBeDefined();
    expect(typeof devWing!.drawerCount).toBe("number");
  });

  it("meta.revision matches sample.revision", () => {
    expect(scene.meta.revision).toBe(sample.revision);
  });
});
