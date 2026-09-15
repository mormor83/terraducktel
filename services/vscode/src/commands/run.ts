import * as vscode from "vscode";
import type { Session } from "../session";
import type { Run } from "../api/types";
import type { RunOutputManager } from "../output/runOutput";
import type { PlanDocumentProvider } from "../output/planDocument";
import { RunNode, WorkspaceNode } from "../views/nodes";
import { wrap } from "./auth";

async function pickRun(s: Session, filter?: (r: Run) => boolean): Promise<Run | undefined> {
  const runs = s.store.runs.filter(filter ?? (() => true));
  const pick = await vscode.window.showQuickPick(runs.map((r) => ({ label: `${s.store.workspace(r.workspace_id)?.name ?? r.workspace_id} · ${r.command}`, description: `${r.status} · ${r.id.slice(0, 8)}`, run: r })), { placeHolder: "Run" });
  return pick?.run;
}
const asRun = async (s: Session, arg: unknown, filter?: (r: Run) => boolean) => (arg instanceof RunNode ? arg.run : pickRun(s, filter));
const wsName = (s: Session, r: Run) => s.store.workspace(r.workspace_id)?.name ?? r.workspace_id.slice(0, 8);

/** `onAwaiting` is told about a run that landed in `awaiting_approval` BEFORE the tail's own
 *  toast goes up — extension.ts points it at `ApprovalWatcher.markSeen`, so the background
 *  approval poll does not announce a second time a run this window already announced. */
export function registerRunCommands(ctx: vscode.ExtensionContext, s: Session, out: RunOutputManager, plans: PlanDocumentProvider, onAwaiting?: (run: Run) => Promise<void> | void) {
  const announceAwaiting = async (landed: Run) => {
    // Best-effort dedupe: if marking it seen fails we would rather show the toast twice than
    // not at all, so a rejection here never stops the toast below.
    try { await onAwaiting?.(landed); }
    catch { /* dedupe is advisory */ }
    const a = await vscode.window.showInformationMessage(`TDT: ${wsName(s, landed)} ${landed.command} is awaiting approval.`, "Show plan", "Approve…");
    if (a === "Show plan") void plans.open(landed.id, wsName(s, landed));
    if (a === "Approve…") void vscode.commands.executeCommand("terraducktel.approve", new RunNode(landed));
  };
  const watch = (r: Run) =>
    out.watch(s.requireClient(), r.id, wsName(s, r), (landed) => {
      void s.store.refresh();
      if (landed.status === "awaiting_approval") void announceAwaiting(landed);
      else if (landed.status === "failed") void vscode.window.showErrorMessage(`TDT: ${wsName(s, landed)} ${landed.command} failed — see the run output.`);
    });

  ctx.subscriptions.push(
    vscode.commands.registerCommand(
      "terraducktel.watchRun",
      wrap(async (arg) => {
        const r = await asRun(s, arg);
        if (r) watch(r);
      }),
    ),
    vscode.commands.registerCommand(
      "terraducktel.showPlan",
      wrap(async (arg) => {
        const r = await asRun(s, arg);
        if (r) await plans.open(r.id, wsName(s, r));
      }),
    ),
    vscode.commands.registerCommand(
      "terraducktel.approve",
      wrap(async (arg) => {
        const c = s.requireClient();
        const r = await asRun(s, arg, (x) => x.status === "awaiting_approval");
        if (!r) return;
        const g = await c.getGraph(r.id).catch(() => undefined);
        const sm = g?.summary ?? {};
        const msg = `Approve ${r.command} on ${wsName(s, r)}?\n\n+${sm.add ?? 0} to add, ~${sm.change ?? 0} to change, -${sm.destroy ?? 0} to destroy, ±${sm.replace ?? 0} to replace.`;
        const a = await vscode.window.showWarningMessage(msg, { modal: true }, "Approve", "Show plan");
        if (a === "Show plan") { await plans.open(r.id, wsName(s, r)); return; }
        if (a !== "Approve") return;
        await c.approve(r.id);
        void vscode.window.showInformationMessage(`TDT: approved ${wsName(s, r)} ${r.command}.`);
        await s.store.refresh();
        watch(r);
      }),
    ),
    vscode.commands.registerCommand(
      "terraducktel.reject",
      wrap(async (arg) => {
        const c = s.requireClient();
        const r = await asRun(s, arg, (x) => x.status === "awaiting_approval");
        if (!r) return;
        const reason = await vscode.window.showInputBox({ prompt: `Reject ${r.command} on ${wsName(s, r)} — reason (optional)` });
        if (reason === undefined) return;
        await c.reject(r.id, reason || undefined);
        await s.store.refresh();
      }),
    ),
    vscode.commands.registerCommand(
      "terraducktel.cancelRun",
      wrap(async (arg) => {
        const c = s.requireClient();
        const r = await asRun(s, arg, (x) => ["pending", "running", "planning", "awaiting_approval"].includes(x.status));
        if (!r) return;
        await c.cancel(r.id);
        await s.store.refresh();
      }),
    ),
    vscode.commands.registerCommand(
      "terraducktel.openInBrowser",
      wrap(async (arg) => {
        const ui = s.uiUrl(); if (!ui) throw new Error("No profile.");
        const url = arg instanceof RunNode ? `${ui}/runs/${arg.run.id}` : arg instanceof WorkspaceNode ? `${ui}/` : `${ui}/runs`;
        await vscode.env.openExternal(vscode.Uri.parse(url));
      }),
    ),
    vscode.commands.registerCommand(
      "terraducktel.copyId",
      wrap(async (arg) => {
        const id = arg instanceof RunNode ? arg.run.id : arg instanceof WorkspaceNode ? arg.ws.id : undefined;
        if (!id) return;
        await vscode.env.clipboard.writeText(id);
        void vscode.window.setStatusBarMessage(`Copied ${id}`, 2000);
      }),
    ),
  );
  return { watch };
}
