import { fileURLToPath, URL } from "node:url";

import { defineConfig } from "vitest/config";

export default defineConfig({
  // Next keeps tsconfig jsx=preserve for its own SWC pipeline. Vite 8/oxc would
  // otherwise leave JSX untouched and fail import analysis on .tsx tests.
  oxc: {
    jsx: {
      runtime: "automatic",
    },
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "node",
  },
});
