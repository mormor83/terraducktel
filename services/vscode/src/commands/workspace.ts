import * as vscode from "vscode";
import type { Session } from "../session";
import type { Run, Workspace } from "../api/types";
import { WorkspaceNode } from "../views/nodes";
import { wrap } from "./auth";

async function pickWorkspace(s: Session): Promise<Workspace | undefined> {
  const pick = await vscode.window.showQuickPick(
    s.store.workspaces.map((w) => ({ label: w.name, description: w.tf_working_dir, detail: `${w.environment} · ${w.repo_ref}`, ws: w })),
    { placeHolder: "Workspace", matchOnDescription: true },
  );
  return pick?.ws;
}
const asWs = async (s: Session, arg: unknown) => (arg instanceof WorkspaceNode ? arg.ws : pickWorkspace(s));

/** Behaviour shared by the Plan/Apply/Destroy commands and the editor status bar's "Plan this
 *  leaf" action: the Apply modal and Destroy type-the-name guard stay in effect regardless of
 *  the caller. When `opts.branch` differs from the workspace's tracked branch, it is pinned via
 *  `updateWorkspace` before the run is triggered. */
export async function runCommandFor(s: Session, ws: Workspace, command: "plan" | "apply" | "destroy", watch: (r: Run) => void, opts: { branch?: string } = {}): Promise<void> {
  const c = s.requireClient();
  if (command === "apply") {
    const ok = await vscode.window.showWarningMessage(`Apply ${ws.name}? The plan will pause for approval before anything changes.`, { modal: true }, "Start apply");
    if (ok !== "Start apply") return;
  }
  if (command === "destroy") {
    const typed = await vscode.window.showInputBox({ prompt: `Type the workspace name to confirm DESTROY: ${ws.name}`, validateInput: (v) => (v === ws.name ? undefined : "Name does not match") });
    if (typed !== ws.name) return;
  }
  if (opts.branch && opts.branch !== ws.repo_ref) await c.updateWorkspace(ws.id, { repo_ref: opts.branch });
  const run = await c.triggerRun(ws.id, { command });
  void vscode.window.showInformationMessage(`TDT: ${command} started on ${ws.name} (${run.id.slice(0, 8)}).`);
  await s.store.refresh();
  watch(run);
}

export function registerWorkspaceCommands(ctx: vscode.ExtensionContext, s: Session, watch: (r: Run) => void) {
  const trigger = async (arg: unknown, command: "plan" | "apply" | "destroy") => {
    const ws = await asWs(s, arg); if (!ws) return;
    await runCommandFor(s, ws, command, watch);
  };

  ctx.subscriptions.push(
    vscode.commands.registerCommand("terraducktel.plan", wrap((a) => trigger(a, "plan"))),
    vscode.commands.registerCommand("terraducktel.apply", wrap((a) => trigger(a, "apply"))),
    vscode.commands.registerCommand("terraducktel.destroy", wrap((a) => trigger(a, "destroy"))),
    vscode.commands.registerCommand(
      "terraducktel.setBranch",
      wrap(async (arg) => {
        const c = s.requireClient();
        const ws = await asWs(s, arg); if (!ws) return;
        const b = await c.listBranches(ws.id).catch(() => ({ source: "none", branches: [] as string[] }));
        let ref: string | undefined;
        if (b.branches.length) {
          const pick = await vscode.window.showQuickPick([...b.branches.map((x) => ({ label: x })), { label: "$(edit) Other…" }], { placeHolder: `Tracked branch for ${ws.name} (current: ${ws.repo_ref})` });
          if (!pick) return;
          ref = pick.label.startsWith("$(edit)") ? await vscode.window.showInputBox({ prompt: "Branch / ref", value: ws.repo_ref }) : pick.label;
        } else {
          ref = await vscode.window.showInputBox({ prompt: `Tracked branch for ${ws.name}`, value: ws.repo_ref });
        }
        if (!ref || ref === ws.repo_ref) return;
        await c.updateWorkspace(ws.id, { repo_ref: ref });
        await s.store.refresh();
      }),
    ),
    vscode.commands.registerCommand(
      "terraducktel.syncWorkspace",
      wrap(async (arg) => {
        const c = s.requireClient();
        const ws = await asWs(s, arg); if (!ws) return;
        await c.syncWorkspace(ws.id);
        await s.store.refresh();
      }),
    ),
  );
}
