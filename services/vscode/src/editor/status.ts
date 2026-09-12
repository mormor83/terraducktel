// services/vscode/src/editor/status.ts
import * as vscode from "vscode";
import type { Session } from "../session";
import type { Run, Workspace } from "../api/types";
import { GitProbe, type GitInfo } from "./git";
import { matchWorkspace, relativeDir } from "./mapping";
import { runCommandFor } from "../commands/workspace";
import type { PlanDocumentProvider } from "../output/planDocument";
import { CTX_FILE_MAPPED } from "../ids";
import { wrap } from "../commands/auth";

const TF_LANGS = new Set(["terraform", "terraform-vars", "hcl"]);
const isTfFile = (doc: vscode.TextDocument) => doc.uri.scheme === "file" && (TF_LANGS.has(doc.languageId) || /\.(tf|tfvars|hcl)$/i.test(doc.uri.fsPath));

export interface CurrentFile { ws: Workspace; git?: GitInfo; exact: boolean }

/** One status-bar item that says which TDT workspace the active Terraform file belongs to. */
export class EditorStatus implements vscode.Disposable {
  private item: vscode.StatusBarItem;
  private git = new GitProbe();
  private cur: CurrentFile | undefined;
  private subs: vscode.Disposable[] = [];
  private seq = 0;

  constructor(private readonly s: Session, private readonly deps: { watch: (r: Run) => void; plans: PlanDocumentProvider; reveal: (wsId: string) => Promise<void> }) {
    this.item = vscode.window.createStatusBarItem("terraducktel.currentFile", vscode.StatusBarAlignment.Left, 50);
    this.item.name = "Terraducktel workspace"; this.item.command = "terraducktel.currentFileActions";
    this.subs.push(this.item,
      vscode.window.onDidChangeActiveTextEditor(() => void this.refresh()),
      vscode.workspace.onDidSaveTextDocument(() => this.git.invalidate()),           // branch may have changed via a commit
      s.store.onDidChange(() => void this.refresh()), s.onDidChange(() => void this.refresh()),
      vscode.workspace.onDidChangeConfiguration((e) => { if (e.affectsConfiguration("terraducktel.statusBar")) void this.refresh(); }),
      vscode.commands.registerCommand("terraducktel.currentFileActions", wrap(() => this.actions())),
      vscode.commands.registerCommand("terraducktel.planCurrentFile", wrap(() => this.planCurrent())),
      vscode.commands.registerCommand("terraducktel.revealCurrentWorkspace", wrap(async () => { if (this.cur) await this.deps.reveal(this.cur.ws.id); })),
    );
    void this.refresh();
  }
  current() { return this.cur; }

  async refresh(): Promise<void> {
    const my = ++this.seq;
    const enabled = vscode.workspace.getConfiguration("terraducktel").get<boolean>("statusBar.enabled", true);
    const doc = vscode.window.activeTextEditor?.document;
    if (!enabled || !doc || !isTfFile(doc) || !this.s.tokens?.isSignedIn()) { this.set(undefined, undefined, false); return; }
    const git = await this.git.info(doc.uri.fsPath);
    if (my !== this.seq) return;                                            // a newer refresh superseded this one
    let match: ReturnType<typeof matchWorkspace>;
    if (git) { const rel = relativeDir(git.root, doc.uri.fsPath); if (rel !== undefined) match = matchWorkspace(this.s.store.workspaces, { relativeDir: rel, remoteUrl: git.remoteUrl }); }
    this.set(match ? { ws: match.ws, git, exact: match.exact } : undefined, git, true);
  }

  private set(cur: CurrentFile | undefined, git: GitInfo | undefined, showUnmapped: boolean) {
    this.cur = cur;
    void vscode.commands.executeCommand("setContext", CTX_FILE_MAPPED, !!cur);
    if (cur) {
      const last = this.s.store.runsFor(cur.ws.id)[0];
      const branchNote = git?.branch && git.branch !== cur.ws.repo_ref ? ` · on ${git.branch} (tracks ${cur.ws.repo_ref})` : "";
      this.item.text = `$(cloud) TDT: ${cur.ws.name}${last ? ` · ${last.status}` : ""}`;
      this.item.tooltip = `${cur.ws.tf_working_dir}${cur.exact ? "" : " (parent leaf)"}${branchNote}\nClick for actions`;
      this.item.backgroundColor = last?.status === "failed" ? new vscode.ThemeColor("statusBarItem.errorBackground") : last?.status === "awaiting_approval" ? new vscode.ThemeColor("statusBarItem.warningBackground") : undefined;
      this.item.show();
    } else if (showUnmapped) {
      this.item.text = "$(cloud) TDT: not imported"; this.item.tooltip = git ? "No Terraducktel workspace covers this path. Click to open Discover in the browser." : "Not inside a git checkout Terraducktel knows about."; this.item.backgroundColor = undefined; this.item.show();
    } else this.item.hide();
  }

  private async actions() {
    if (!this.cur) { const ui = this.s.uiUrl(); if (ui) await vscode.env.openExternal(vscode.Uri.parse(`${ui}/`)); return; }
    const { ws, git } = this.cur; const last = this.s.store.runsFor(ws.id)[0];
    type Item = vscode.QuickPickItem & { act: () => Promise<unknown> };
    const items: Item[] = [
      { label: "$(play) Plan this leaf", description: ws.name, act: () => this.planCurrent() },
      ...(last ? [{ label: "$(diff) Show last plan", description: `${last.command} · ${last.status}`, act: () => this.deps.plans.open(last.id, ws.name) }] : []),
      { label: "$(list-tree) Reveal in sidebar", act: () => this.deps.reveal(ws.id) },
      { label: "$(link-external) Open in browser", act: async () => { const ui = this.s.uiUrl(); if (ui) await vscode.env.openExternal(vscode.Uri.parse(`${ui}/`)); } },
    ];
    const pick = await vscode.window.showQuickPick(items, { placeHolder: `${ws.name} · ${ws.tf_working_dir}${git?.branch ? ` · branch ${git.branch}` : ""}` });
    if (pick) await pick.act().catch((e) => vscode.window.showErrorMessage(`Terraducktel: ${e instanceof Error ? e.message : String(e)}`));
  }

  private async planCurrent() {
    if (!this.cur) { void vscode.window.showInformationMessage("Terraducktel: the active file is not inside an imported workspace."); return; }
    const { ws, git } = this.cur;
    let branch: string | undefined;
    if (git?.branch && git.branch !== ws.repo_ref) {
      const pick = await vscode.window.showQuickPick([
        { label: `Plan on ${git.branch}`, description: "pins the workspace to this branch", b: git.branch },
        { label: `Plan on ${ws.repo_ref}`, description: "the workspace's tracked branch", b: undefined as string | undefined },
      ], { placeHolder: `Checked out ${git.branch}, workspace tracks ${ws.repo_ref}` });
      if (!pick) return; branch = pick.b;
    }
    await runCommandFor(this.s, ws, "plan", this.deps.watch, { branch });
  }
  dispose() { for (const d of this.subs) d.dispose(); }
}
