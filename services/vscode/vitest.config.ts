import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["test/unit/**/*.test.ts"],
    environment: "node",
    // The real `vscode` module only exists inside the editor; unit tests
    // exercise pure modules and get a stub for anything that imports it.
    alias: { vscode: new URL("./test/unit/vscode-stub.ts", import.meta.url).pathname },
  },
});
