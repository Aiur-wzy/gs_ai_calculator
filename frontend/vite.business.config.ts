import {fileURLToPath} from "node:url";
import {defineConfig} from "vite";
import {sourceProvenancePlugin} from "./scripts/build-source-provenance.mjs";
export default defineConfig({plugins:[sourceProvenancePlugin(fileURLToPath(new URL(".",import.meta.url)),"business")],build:{outDir:"dist/business",emptyOutDir:true,sourcemap:true,
  lib:{entry:fileURLToPath(new URL("./src/business/entry.ts",import.meta.url)),name:"GenshinBusinessUI",formats:["iife"],fileName:()=>"business-ui.js"}}});
