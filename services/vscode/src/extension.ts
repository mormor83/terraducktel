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

export async function activate(context: vscode.ExtensionContext): Promise<void> {
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
}
export function deactivate(): void {}
