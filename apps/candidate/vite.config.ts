import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies the API and the interview socket to the backend, so the
// app runs on one origin in dev exactly as it does in production (where FastAPI
// serves the built bundle itself).
const ui = fileURLToPath(new URL("../../packages/ui", import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@tara/ui": ui } },
  server: {
    port: 5173,
    // The shared UI package lives outside this app's root.
    fs: { allow: [fileURLToPath(new URL("../..", import.meta.url))] },
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
