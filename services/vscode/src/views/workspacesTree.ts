import * as vscode from "vscode";
import type { Session } from "../session";
import { buildTree, type CloudGroup, type FolderNode } from "../state/grouping";
import { CloudNode, FolderTreeNode, MessageNode, RegionNode, RunNode, WorkspaceNode, type Node } from "./nodes";

export class WorkspacesTree implements vscode.TreeDataProvider<Node> {
  private changed = new vscode.EventEmitter<Node | undefined>();
  readonly onDidChangeTreeData = this.changed.event;
  private tree: CloudGroup[] = [];
  constructor(private readonly s: Session) {
    s.store.onDidChange(() => { this.tree = buildTree(s.store.workspaces); this.changed.fire(undefined); });
    s.onDidChange(() => this.changed.fire(undefined));
  }
  getTreeItem(n: Node) { return n; }
  getChildren(n?: Node): Node[] {
    if (!n) {
      if (!this.s.profile) return [new MessageNode("No profile configured — Settings → Terraducktel", "gear")];
      if (!this.s.tokens?.isSignedIn()) return [];
      const head: Node[] = [];
      if (this.s.store.lastError) head.push(new MessageNode(`Refresh failed: ${this.s.store.lastError.message}`, "warning"));
      if (this.s.profile.insecureTls) head.push(new MessageNode("TLS verification is OFF for this profile", "shield"));
      if (!this.tree.length && !this.s.store.lastError) head.push(new MessageNode(`No workspaces in BU “${this.s.bu || "default"}”`));
      return [...head, ...this.tree.map((g) => new CloudNode(g))];
    }
    if (n instanceof CloudNode) return n.group.regions.map((r) => new RegionNode(n.group, r));
    if (n instanceof RegionNode) return this.folderChildren(n.region.root, `${n.group.key}/${n.region.region}`);
    if (n instanceof FolderTreeNode) return this.folderChildren(n.folder, n.path);
    if (n instanceof WorkspaceNode) return this.s.store.runsFor(n.ws.id).slice(0, 10).map((r) => new RunNode(r));
    return [];
  }
  private folderChildren(f: FolderNode, path: string): Node[] {
    const folders = [...f.folders.values()].map((c) => new FolderTreeNode(c, `${path}/${c.name}`));
    const leaves = f.workspaces.map(({ ws, leaf }) => { const runs = this.s.store.runsFor(ws.id); return new WorkspaceNode(ws, leaf, runs[0], runs.length); });
    return [...folders, ...leaves];
  }
  getParent(): undefined { return undefined; }
}
