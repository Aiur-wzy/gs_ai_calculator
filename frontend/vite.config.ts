import {fileURLToPath, URL} from "node:url";
import {defineConfig} from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "./",
  plugins: [react()],
  resolve: {
    alias: {"@": fileURLToPath(new URL("./src", import.meta.url))},
  },
  build: {
    manifest: true,
    sourcemap: true,
    rollupOptions: {
      input: {
        gallery: fileURLToPath(new URL("./index.html", import.meta.url)),
        "legacy-foundation": fileURLToPath(new URL("./src/legacy-entry.ts", import.meta.url)),
      },
      output: {
        entryFileNames: "assets/[name]-[hash].js",
        chunkFileNames: "assets/[name]-[hash].js",
        assetFileNames: "assets/[name]-[hash][extname]",
      },
    },
  },
  test: {
    environment: "jsdom",
    environmentOptions: {
      jsdom: {
        // The DSH Client Module Loader receives reviewed classic-script bytes
        // from ShellCarrier.  Integration tests must exercise that real
        // registration path rather than replacing it with eval/Function.
        runScripts: "dangerously",
      },
    },
    restoreMocks: true,
    clearMocks: true,
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    server: {
      deps: {
        // DSH's public web boot kernel intentionally imports its own CSS.
        // Inline it so Vitest routes those assets through Vite instead of
        // handing the package to Node's CSS-blind ESM loader.
        inline: [
          "@deepseek-ai/dsh-client-web",
          "@deepseek-ai/dsh-client-store",
          "@deepseek-ai/dsh-client-ui-slots",
          "@deepseek-ai/dsh-client-ui-primitives",
          "@deepseek-ai/dsh-client-ui-dockkit",
        ],
      },
    },
  },
});
