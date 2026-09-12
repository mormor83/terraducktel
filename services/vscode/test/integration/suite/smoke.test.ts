import * as assert from "node:assert";
import * as vscode from "vscode";

suite("Terraducktel extension smoke", () => {
  test("activates, signs in with an API key against the stub, renders workspaces, triggers a plan", async () => {
    const ext = vscode.extensions.getExtension("terraducktel.terraducktel-vscode")!;
    assert.ok(ext, "extension not found");
    const cfg = vscode.workspace.getConfiguration("terraducktel");
    await cfg.update("profiles", [{ name: "stub", url: process.env.TDT_STUB_URL, bu: "b" }], vscode.ConfigurationTarget.Global);
    await cfg.update("activeProfile", "stub", vscode.ConfigurationTarget.Global);
    await ext.activate();
    // Sign in without UI: store the API key the way the sign-in command would.
    await ext.exports?.__test?.signInWithApiKey?.("tdt_smoke");
    await new Promise((r) => setTimeout(r, 1500));
    const names = await ext.exports.__test.workspaceNames();
    assert.deepStrictEqual(names, ["vpc"]);
    const run = await ext.exports.__test.triggerPlan("w1");
    assert.strictEqual(run.id, "r2");
    await new Promise((r) => setTimeout(r, 1500));
    const runIds = await ext.exports.__test.runIds();
    assert.ok(runIds.includes("r2"));
  });

  test("registers editor integration commands", async () => {
    const commands = await vscode.commands.getCommands(true);
    assert.ok(commands.includes("terraducktel.currentFileActions"), "terraducktel.currentFileActions command not registered");
    assert.ok(commands.includes("terraducktel.planCurrentFile"), "terraducktel.planCurrentFile command not registered");
    assert.ok(commands.includes("terraducktel.revealCurrentWorkspace"), "terraducktel.revealCurrentWorkspace command not registered");
  });

  test("declares the approvals poll setting with a 60s default", () => {
    const inspect = vscode.workspace.getConfiguration("terraducktel").inspect<number>("approvals.pollSeconds");
    assert.strictEqual(inspect?.defaultValue, 60);
  });
});
