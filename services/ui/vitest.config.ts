import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Unit/component tests run under jsdom. The Playwright end-to-end specs under
// tests/e2e use the Playwright runner (`npm run test:e2e`) and must be excluded
// here — otherwise vitest's default glob collects them and fails.
export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    exclude: ["node_modules", "dist", "tests/e2e/**"],
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.{ts,tsx}", "src/test/**", "src/main.tsx", "**/*.d.ts"],
      reporter: ["text-summary", "text"],
    },
  },
});
