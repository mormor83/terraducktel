import * as vscode from "vscode";
import type { TdtClient } from "../api/client";
import { PLAN_SCHEME } from "../ids";

export type LineKind = "add" | "change" | "destroy" | "replace" | "other";

export function planLineKinds(text: string): LineKind[] {
  return text.split("\n").map((l) => {
    const t = l.replace(/^\s+/, "");
    if (t.startsWith("-/+") || t.startsWith("+/-")) return "replace";
    if (t.startsWith("+ ") || t.startsWith("+resource") || /^\+\s*$/.test(t)) return "add";
    if (t.startsWith("~ ")) return "change";
    if (t.startsWith("- ")) return "destroy";
    return "other";
  });
}

export function planUri(runId: string, label: string) {
  return vscode.Uri.parse(`${PLAN_SCHEME}:${encodeURIComponent(label)}.tfplan.txt?run=${runId}`);
}

export class PlanDocumentProvider implements vscode.TextDocumentContentProvider, vscode.Disposable {
  private cache = new Map<string, string>();
  private changed = new vscode.EventEmitter<vscode.Uri>();
  readonly onDidChange = this.changed.event;
  private decos: Record<Exclude<LineKind, "other">, vscode.TextEditorDecorationType> = {
    add: vscode.window.createTextEditorDecorationType({ isWholeLine: true, backgroundColor: new vscode.ThemeColor("diffEditor.insertedLineBackground") }),
    destroy: vscode.window.createTextEditorDecorationType({ isWholeLine: true, backgroundColor: new vscode.ThemeColor("diffEditor.removedLineBackground") }),
    change: vscode.window.createTextEditorDecorationType({ isWholeLine: true, backgroundColor: new vscode.ThemeColor("editor.wordHighlightBackground") }),
    replace: vscode.window.createTextEditorDecorationType({ isWholeLine: true, backgroundColor: new vscode.ThemeColor("editorWarning.background"), border: "0 0 0 2px solid", borderColor: new vscode.ThemeColor("editorWarning.foreground") }),
  };
  private subs: vscode.Disposable[] = [];

  constructor(private readonly client: () => TdtClient | undefined) {
    this.subs.push(
      vscode.workspace.registerTextDocumentContentProvider(PLAN_SCHEME, this),
      vscode.window.onDidChangeActiveTextEditor((ed) => ed && this.decorate(ed)),
    );
  }

  /** Async on purpose: a plan tab restored by VS Code on the next window reload asks for its
   *  content with an empty cache, so fall back to fetching the run id out of the URI's query
   *  rather than leaving the reopened tab stuck on "(loading…)". */
  async provideTextDocumentContent(uri: vscode.Uri): Promise<string> {
    const hit = this.cache.get(uri.toString());
    if (hit !== undefined) return hit;
    const runId = new URLSearchParams(uri.query).get("run");
    if (!runId) return "(no run id in this plan URI)";
    const c = this.client(); if (!c) return "(not signed in — run “Terraducktel: Sign in”, then reopen this plan)";
    try {
      const text = await this.fetch(c, runId, uri);
      return text;
    } catch (e) {
      return `(could not load the plan: ${e instanceof Error ? e.message : String(e)})`;
    }
  }

  private async fetch(c: TdtClient, runId: string, uri: vscode.Uri): Promise<string> {
    const { plan_output } = await c.getPlan(runId);
    const text = plan_output?.trim() ? plan_output : "(no plan output recorded for this run yet)";
    this.cache.set(uri.toString(), text);
    return text;
  }

  async open(runId: string, label: string): Promise<void> {
    const c = this.client(); if (!c) throw new Error("Not signed in.");
    const uri = planUri(runId, label);
    await this.fetch(c, runId, uri);
    this.changed.fire(uri);
    const doc = await vscode.workspace.openTextDocument(uri);
    // Best-effort: gives HCL syntax colouring on top of the diff decorations when a Terraform
    // grammar is installed, and is a no-op (throws "Unknown language id") when none is.
    try { await vscode.languages.setTextDocumentLanguage(doc, "terraform"); } catch { /* no terraform grammar installed */ }
    const ed = await vscode.window.showTextDocument(doc, { preview: true });
    this.decorate(ed);
  }

  private decorate(ed: vscode.TextEditor) {
    if (ed.document.uri.scheme !== PLAN_SCHEME) return;
    const kinds = planLineKinds(ed.document.getText());
    const by: Record<string, vscode.Range[]> = { add: [], change: [], destroy: [], replace: [] };
    kinds.forEach((k, i) => { if (k !== "other") by[k].push(ed.document.lineAt(i).range); });
    for (const k of Object.keys(this.decos) as Array<keyof typeof this.decos>) ed.setDecorations(this.decos[k], by[k]);
  }

  dispose() {
    for (const d of this.subs) d.dispose();
    for (const d of Object.values(this.decos)) d.dispose();
    this.changed.dispose();
  }
}
