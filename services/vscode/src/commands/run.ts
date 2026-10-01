import * as vscode from "vscode";
import type { Session } from "../session";
import type { GraphSummary, Run } from "../api/types";
import type { RunOutputManager } from "../output/runOutput";
import type { PlanDocumentProvider } from "../output/planDocument";
import { RunNode, WorkspaceNode } from "../views/nodes";
import { wrap } from "./auth";

/** `+2 to add, ~1 to change, -2 to destroy[, ±1 to replace]` — replace only when non-zero. */
export function planSummaryText(sm: GraphSummary): string {
  const parts = [`+${sm.add ?? 0} to add`, `~${sm.change ?? 0} to change`, `-${sm.destroy ?? 0} to destroy`];
  if (sm.replace) parts.push(`±${sm.replace} to replace`);
  return parts.join(", ");
}

/** A run together with the BU it lives in — every action on it is made through `s.clientFor(bu)`. */
export interface BuRun { run: Run; bu: string }

/** Command-palette fallback: pick among the runs of ALL visible BUs ("workspace · command", BU in the detail line). */
async function pickRun(s: Session, filter?: (r: Run) => boolean): Promise<BuRun | undefined> {
  const items = [...s.store.data.values()].flatMap((d) => d.runs.filter(filter ?? (() => true)).map((r) => ({ label: `${d.workspaces.find((w) => w.id === r.workspace_id)?.name ?? r.workspace_id} · ${r.command}`, description: `${r.status} · ${r.id.slice(0, 8)}`, detail: d.bu.name || d.bu.slug, pick: { run: r, bu: d.bu.slug } })));
  return (await vscode.window.showQuickPick(items, { placeHolder: "Run" }))?.pick;
}
const asRun = async (s: Session, arg: unknown, filter?: (r: Run) => boolean): Promise<BuRun | undefined> => (arg instanceof RunNode ? { run: arg.run, bu: arg.bu } : pickRun(s, filter));
const wsName = (s: Session, r: Run) => s.store.workspace(r.workspace_id)?.name ?? r.workspace_id.slice(0, 8);

/** `onAwaiting` is told about a run that landed in `awaiting_approval` BEFORE the tail's own
 *  toast goes up — extension.ts points it at `ApprovalWatcher.markSeen`, so the background
 *  approval poll does not announce a second time a run this window already announced. */
export function registerRunCommands(ctx: vscode.ExtensionContext, s: Session, out: RunOutputManager, plans: PlanDocumentProvider, onAwaiting?: (run: Run) => Promise<void> | void) {
  const announceAwaiting = async (landed: Run, bu: string) => {
    // Best-effort dedupe: if marking it seen fails we would rather show the toast twice than
    // not at all, so a rejection here never stops the toast below.
    try { await onAwaiting?.(landed); }
    catch { /* dedupe is advisory */ }
    const buName = s.store.data.get(bu)?.bu.name || bu;
    const a = await vscode.window.showInformationMessage(`TDT: ${wsName(s, landed)} (${buName}) ${landed.command} is awaiting approval.`, "Show plan", "Approve…");
    if (a === "Show plan") void plans.open(landed.id, wsName(s, landed), bu);
    if (a === "Approve…") void vscode.commands.executeCommand("terraducktel.approve", new RunNode(landed, { bu }));
  };
  const watch = (r: Run, bu: string) =>
    out.watch(s.clientFor(bu), r.id, wsName(s, r), (landed) => {
      void s.store.refresh();
      if (landed.status === "awaiting_approval") void announceAwaiting(landed, bu);
      else if (landed.status === "failed") void vscode.window.showErrorMessage(`TDT: ${wsName(s, landed)} ${landed.command} failed — see the run output.`);
    });

  ctx.subscriptions.push(
    vscode.commands.registerCommand(
      "terraducktel.watchRun",
      wrap(async (arg) => {
        const t = await asRun(s, arg);
        if (t) watch(t.run, t.bu);
      }),
    ),
    vscode.commands.registerCommand(
      "terraducktel.showPlan",
      wrap(async (arg) => {
        const t = await asRun(s, arg);
        if (t) await plans.open(t.run.id, wsName(s, t.run), t.bu);
      }),
    ),
    vscode.commands.registerCommand(
      "terraducktel.approve",
      wrap(async (arg) => {
        const t = await asRun(s, arg, (x) => x.status === "awaiting_approval");
        if (!t) return;
        const { run: r, bu } = t; const c = s.clientFor(bu);
        const g = await c.getGraph(r.id).catch(() => undefined);
        const sm = g?.summary ?? {};
        const a = await vscode.window.showInformationMessage(`Approve ${r.command} on ${wsName(s, r)}?`, { modal: true, detail: planSummaryText(sm) }, "Approve", "Show plan");
        if (a === "Show plan") { await plans.open(r.id, wsName(s, r), bu); return; }
        if (a !== "Approve") return;
        await c.approve(r.id);
        void vscode.window.showInformationMessage(`TDT: approved ${wsName(s, r)} ${r.command}.`);
        await s.store.refresh();
        watch(r, bu);
      }),
    ),
    vscode.commands.registerCommand(
      "terraducktel.reject",
      wrap(async (arg) => {
        const t = await asRun(s, arg, (x) => x.status === "awaiting_approval");
        if (!t) return;
        const { run: r } = t;
        const reason = await vscode.window.showInputBox({ prompt: `Reject ${r.command} on ${wsName(s, r)} — reason (optional)` });
        if (reason === undefined) return;
        await s.clientFor(t.bu).reject(r.id, reason || undefined);
        await s.store.refresh();
      }),
    ),
    vscode.commands.registerCommand(
      "terraducktel.cancelRun",
      wrap(async (arg) => {
        const t = await asRun(s, arg, (x) => ["pending", "running", "planning", "awaiting_approval"].includes(x.status));
        if (!t) return;
        await s.clientFor(t.bu).cancel(t.run.id);
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
