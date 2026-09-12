import * as vscode from "vscode";
import { registerAuthCommands } from "./commands/auth";
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
  await session.reload();
}
export function deactivate(): void {}
