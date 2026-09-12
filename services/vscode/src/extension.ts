import * as vscode from "vscode";
import { registerAuthCommands } from "./commands/auth";
import { registerRunCommands } from "./commands/run";
import { registerWorkspaceCommands } from "./commands/workspace";
import { RunOutputManager } from "./output/runOutput";
import { PlanDocumentProvider } from "./output/planDocument";
import { Session } from "./session";
import { RunsTree } from "./views/runsTree";
import { WorkspacesTree } from "./views/workspacesTree";
import { VIEW_RUNS, VIEW_WORKSPACES } from "./ids";

/** Test-only export surface returned from `activate`, exercised by the headless integration
 *  smoke test (test/integration/suite/smoke.test.ts). Nothing in the extension calls this
 *  itself — it exists purely so the smoke can sign in / read state / trigger a run without
 *  driving any UI. */
export interface TestSurface {
  __test: {
    signInWithApiKey: (key: string) => Promise<void>;
    workspaceNames: () => Promise<string[]>;
    runIds: () => Promise<string[]>;
    triggerPlan: (id: string) => ReturnType<import("./api/client").TdtClient["triggerRun"]>;
  };
}

export async function activate(context: vscode.ExtensionContext): Promise<TestSurface> {
  const session = new Session(context);
  context.subscriptions.push(session);
  const wsTree = new WorkspacesTree(session), runsTree = new RunsTree(session);
  const wsView = vscode.window.createTreeView(VIEW_WORKSPACES, { treeDataProvider: wsTree, showCollapseAll: true });
  const runsView = vscode.window.createTreeView(VIEW_RUNS, { treeDataProvider: runsTree });
  context.subscriptions.push(wsView, runsView);
  const onVis = () => { if (wsView.visible || runsView.visible) void session.store.refresh(); };
  context.subscriptions.push(wsView.onDidChangeVisibility(onVis), runsView.onDidChangeVisibility(onVis));
  session.store.onDidChange(() => { const n = session.store.runs.filter((r) => r.status === "awaiting_approval").length; runsView.badge = n ? { value: n, tooltip: `${n} run(s) awaiting approval` } : undefined; });
  registerAuthCommands(context, session);
  const out = new RunOutputManager(); const plans = new PlanDocumentProvider(() => session.client);
  context.subscriptions.push(out, plans);
  const { watch } = registerRunCommands(context, session, out, plans);
  registerWorkspaceCommands(context, session, watch);
  await session.reload();
  return {
    __test: {
      signInWithApiKey: (k) => session.tokens!.signInWithApiKey(k).then(() => session.store.refresh()),
      workspaceNames: async () => session.store.workspaces.map((w) => w.name),
      runIds: async () => session.store.runs.map((r) => r.id),
      // Mirrors what the real "Plan" command does: trigger, then refresh the store so the new
      // run shows up without waiting for the next poll tick.
      triggerPlan: async (id) => {
        const run = await session.requireClient().triggerRun(id, { command: "plan" });
        await session.store.refresh();
        return run;
      },
    },
  };
}
export function deactivate(): void {}
