import * as vscode from "vscode";
import type { Session } from "../session";
import { MessageNode, RunNode, type Node } from "./nodes";

const ORDER = ["awaiting_approval", "applying", "running", "planning", "pending", "planned", "failed", "applied", "cancelled"];

export class RunsTree implements vscode.TreeDataProvider<Node> {
  private changed = new vscode.EventEmitter<Node | undefined>();
  readonly onDidChangeTreeData = this.changed.event;
  constructor(private readonly s: Session) { s.store.onDidChange(() => this.changed.fire(undefined)); s.onDidChange(() => this.changed.fire(undefined)); }
  getTreeItem(n: Node) { return n; }
  getChildren(n?: Node): Node[] {
    if (n || !this.s.tokens?.isSignedIn()) return [];
    const runs = [...this.s.store.runs].sort((a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status) || (b.created_at ?? "").localeCompare(a.created_at ?? ""));
    if (!runs.length) return [new MessageNode("No runs yet")];
    const name = (id: string) => this.s.store.workspace(id)?.name ?? id.slice(0, 8);
    return runs.map((r) => new RunNode(r, { showWorkspace: name(r.workspace_id) }));
  }
}
