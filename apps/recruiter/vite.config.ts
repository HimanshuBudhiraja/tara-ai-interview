import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const ui = fileURLToPath(new URL("../../packages/ui", import.meta.url));

/**
 * The console is served under `/recruiter/` in production, so it is built with
 * that base and dev-served there too. Matching dev to prod means the route
 * parser, the links and the history API all behave the same in both, rather
 * than working locally and breaking on deploy.
 */
export default defineConfig({
  base: "/recruiter/",
  plugins: [react()],
  resolve: { alias: { "@tara/ui": ui } },
  server: {
    port: 5174,
    // The shared UI package lives outside this app's root.
    fs: { allow: [fileURLToPath(new URL("../..", import.meta.url))] },
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
