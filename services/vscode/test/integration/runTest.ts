import * as path from "node:path";
import { runTests } from "@vscode/test-electron";
import { fork } from "node:child_process";

async function main() {
  const extensionDevelopmentPath = path.resolve(__dirname, "../../");
  const extensionTestsPath = path.resolve(__dirname, "./suite/index");
  // Stub TDT API on a fixed port the smoke test's settings point at. `stub-server.js` is plain
  // Node and lives under the SOURCE tree (test/integration/), not out-test/ — tsc doesn't copy
  // .js files, so this path resolves back out of the compiled out-test/integration/ directory.
  const stub = fork(path.resolve(__dirname, "../../test/integration/stub-server.js"), [], {
    env: { ...process.env, STUB_PORT: "48765" },
    stdio: "inherit",
  });
  await new Promise((r) => setTimeout(r, 500));
  try {
    await runTests({
      extensionDevelopmentPath,
      extensionTestsPath,
      launchArgs: ["--disable-extensions", "--disable-gpu"],
      extensionTestsEnv: { TDT_STUB_URL: "http://127.0.0.1:48765" },
    });
  } finally {
    stub.kill();
  }
}
main().catch((e) => {
  console.error(e);
  process.exit(1);
});
