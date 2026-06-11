/**
 * Vite config for the Sage Estate 3D dashboard.
 *
 * Produces a single self-contained HTML at dist/estate.html — no CDN,
 * no external assets. The operator opens the file directly in a browser.
 *
 * vite-plugin-singlefile inlines all JS and CSS into the HTML output.
 *
 * WHERE: src/sage_mcp/estate/web/vite.config.ts
 */

import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";
import { resolve } from "path";
import { fileURLToPath } from "url";

const __dirname = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  plugins: [viteSingleFile()],
  root: __dirname,
  build: {
    outDir: resolve(__dirname, "dist"),
    emptyOutDir: false,
    rollupOptions: {
      input: resolve(__dirname, "estate.html"),
    },
  },
});
