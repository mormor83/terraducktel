import * as vscode from "vscode";
import { registerAuthCommands } from "./commands/auth";
import { registerRunCommands } from "./commands/run";
import { registerWorkspaceCommands } from "./commands/workspace";
import { EditorStatus } from "./editor/status";
import { ApprovalWatcher } from "./notifications/approvals";
import { createRearm } from "./notifications/rearm";
import { RunOutputManager } from "./output/runOutput";
import { PlanDocumentProvider } from "./output/planDocument";
import { Session } from "./session";
import { RunNode } from "./views/nodes";
import { ProfileStatus } from "./views/profileStatus";
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
    setActiveProfile: (name: string) => Promise<void>;
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
  const seenStore = { get: () => context.globalState.get<Record<string, number>>("terraducktel.approvals.seen"), set: (v: Record<string, number>) => Promise.resolve(context.globalState.update("terraducktel.approvals.seen", v)) };
  const approvals = new ApprovalWatcher({
    client: () => (session.tokens?.isSignedIn() ? session.client : undefined),
    workspaceName: (id) => session.store.workspace(id)?.name ?? id.slice(0, 8),
    seen: seenStore,
    trace: (l) => { if (vscode.workspace.getConfiguration("terraducktel").get<boolean>("trace")) session.log.appendLine(l); },
    notify: ({ run, workspaceName, summary }) => {
      const s = summary ? ` (+${summary.add ?? 0} ~${summary.change ?? 0} -${summary.destroy ?? 0})` : "";
      void vscode.window.showInformationMessage(`TDT: ${workspaceName} ${run.command} is awaiting approval${s}.`, "Approve…", "Reject…", "Open").then((a) => {
        const node = new RunNode(run, { showWorkspace: workspaceName });
        if (a === "Approve…") void vscode.commands.executeCommand("terraducktel.approve", node);
        else if (a === "Reject…") void vscode.commands.executeCommand("terraducktel.reject", node);
        else if (a === "Open") void vscode.commands.executeCommand("terraducktel.openInBrowser", node);
      });
    },
  });
  context.subscriptions.push(approvals);

  // The run-output tail already announces a run it was following into awaiting_approval; tell
  // the watcher so the poll loop stays quiet about that one.
  const { watch } = registerRunCommands(context, session, out, plans, (r) => approvals.markSeen(r.id));
  registerWorkspaceCommands(context, session, watch);
  const status = new EditorStatus(session, { watch, plans, reveal: (id) => wsTree.revealWorkspace(wsView, id) });
  const profileStatus = new ProfileStatus(session);
  context.subscriptions.push(status, profileStatus);

  // A hand-edited settings.json can put a string (or anything) in a `number` setting; VS Code
  // hands it straight back, and NaN would otherwise floor to NaN and disable the poll silently.
  const approvalsInterval = () => {
    const s = Number(vscode.workspace.getConfiguration("terraducktel").get("approvals.pollSeconds", 60));
    const secs = Number.isFinite(s) ? s : 60;
    return secs <= 0 ? 0 : Math.max(15, secs) * 1000;
  };
  const rearm = createRearm({
    key: () => (session.tokens?.isSignedIn() ? `${session.profile?.name}:${session.bu}` : undefined),
    prime: () => approvals.prime(),
    start: () => approvals.start(approvalsInterval()),
    stop: () => approvals.stop(),
  });
  context.subscriptions.push(session.onDidChange(() => void rearm()),
    vscode.workspace.onDidChangeConfiguration((e) => { if (e.affectsConfiguration("terraducktel.approvals")) void rearm(); }));
  void rearm();

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
      setActiveProfile: (name) => session.setActiveProfile(name),
    },
  };
}
export function deactivate(): void {}
