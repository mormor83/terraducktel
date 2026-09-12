import * as vscode from "vscode";
import { registerAuthCommands } from "./commands/auth";
import { registerRunCommands } from "./commands/run";
import { registerWorkspaceCommands } from "./commands/workspace";
import { EditorStatus } from "./editor/status";
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
  // The store polls only while one of the two views is on screen; becoming visible also
  // refreshes at once, so an expanded sidebar never shows a stale snapshot from before it
  // was hidden. Explicit refreshes (title-bar button, post-command) run regardless.
  const onVis = () => {
    const visible = wsView.visible || runsView.visible;
    session.store.setActive(visible);
    if (visible) void session.store.refresh();
  };
  session.store.setActive(wsView.visible || runsView.visible);
  context.subscriptions.push(wsView.onDidChangeVisibility(onVis), runsView.onDidChangeVisibility(onVis));
  session.store.onDidChange(() => { const n = session.store.runs.filter((r) => r.status === "awaiting_approval").length; runsView.badge = n ? { value: n, tooltip: `${n} run(s) awaiting approval` } : undefined; });
  registerAuthCommands(context, session);
  const out = new RunOutputManager(); const plans = new PlanDocumentProvider(() => session.client);
  context.subscriptions.push(out, plans);
  const { watch } = registerRunCommands(context, session, out, plans);
  registerWorkspaceCommands(context, session, watch);
  // `revealWorkspace` lands in Task 4; until then, jump to the sidebar view instead of a
  // specific node.
  const status = new EditorStatus(session, { watch, plans, reveal: async () => { await vscode.commands.executeCommand("terraducktel.workspaces.focus"); } });
  context.subscriptions.push(status);
  await session.reload();
  return {
    __test: {
      // The smoke writes the profile settings *after* startup activation, so a reload may still
      // be in flight (and about to replace `session.tokens`) when this is called. Settle it
      // first, then sign in and pull once: the store only polls while signed in AND visible,
      // and a headless smoke never opens the sidebar.
      signInWithApiKey: async (k) => {
        await session.reload();
        await session.tokens!.signInWithApiKey(k);
        await session.store.refresh();
      },
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
