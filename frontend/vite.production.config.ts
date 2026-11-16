import {fileURLToPath, URL} from "node:url";
import {defineConfig} from "vite";
import {productionProvenancePlugin} from "./scripts/production-provenance.mjs";
export default defineConfig({
  plugins: [productionProvenancePlugin(fileURLToPath(new URL(".", import.meta.url)))],
  resolve: {dedupe: ["react", "react-dom"], alias: {"@": fileURLToPath(new URL("./src", import.meta.url)), "node:module": fileURLToPath(new URL("./src/dsh/browser-node-module.ts", import.meta.url))}},
  // Exact official DSH apps/web browser build definitions, not a Node polyfill.
  define: {"process.env.NODE_ENV": '"production"', "process.versions.node": '"0.0.0"', "process.execArgv": "[]", "process.env.CORDIS_SHARED": "undefined"},
  build: {outDir: "dist/production", emptyOutDir: true, sourcemap: true, minify: "esbuild",
    lib: {entry: fileURLToPath(new URL("./src/production-entry.ts", import.meta.url)), name: "GenshinDshProduction", formats: ["iife"], cssFileName: "dsh-production", fileName: () => "dsh-production.js"}},
});
