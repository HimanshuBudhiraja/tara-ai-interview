import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const ui = fileURLToPath(new URL("../../packages/ui", import.meta.url));

/**
 * The console's tests.
 *
 * Separate from `vite.config.ts` because the app is built with `base:
 * "/recruiter/"` and a test run has no base path; sharing one config meant
 * either a base the tests don't want or losing it from the build.
 */
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@tara/ui": ui } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
