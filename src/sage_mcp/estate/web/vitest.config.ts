import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Default environment for layout + 3D scene tests (node — no DOM, no WebGL).
    environment: "node",
    include: ["src/**/*.test.ts", "test/**/*.test.ts"],
    // Per-file environment overrides (e.g. happy-dom for render.test.ts) are
    // declared via a `// @vitest-environment happy-dom` docblock at the top of
    // the file — the `environmentMatchGlobs` config API was removed in vitest 4.
  },
});
