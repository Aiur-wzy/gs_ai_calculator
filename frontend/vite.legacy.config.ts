import {fileURLToPath, URL} from "node:url";
import {defineConfig} from "vite";
import {sourceProvenancePlugin} from "./scripts/build-source-provenance.mjs";

export default defineConfig({
  plugins: [sourceProvenancePlugin(fileURLToPath(new URL(".", import.meta.url)), "foundation")],
  resolve: {
    alias: {"@": fileURLToPath(new URL("./src", import.meta.url))},
  },
  build: {
    outDir: "dist/legacy",
    emptyOutDir: false,
    sourcemap: true,
    minify: "esbuild",
    lib: {
      entry: fileURLToPath(new URL("./src/legacy-entry.ts", import.meta.url)),
      name: "GenshinGuiFoundationV2Bundle",
      formats: ["iife"],
      fileName: () => "gui-foundation-v2.js",
    },
  },
});
