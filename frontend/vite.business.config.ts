import {fileURLToPath} from "node:url";
import {defineConfig} from "vite";
export default defineConfig({build:{outDir:"dist/business",emptyOutDir:true,sourcemap:true,
  lib:{entry:fileURLToPath(new URL("./src/business/entry.ts",import.meta.url)),name:"GenshinBusinessUI",formats:["iife"],fileName:()=>"business-ui.js"}}});
