import * as vscode from "vscode";
import type { Session } from "../session";
import { buildTree, type CloudGroup, type FolderNode } from "../state/grouping";
import { CloudNode, FolderTreeNode, MessageNode, RegionNode, RunNode, WorkspaceNode, type Node } from "./nodes";

export class WorkspacesTree implements vscode.TreeDataProvider<Node> {
  private changed = new vscode.EventEmitter<Node | undefined>();
  readonly onDidChangeTreeData = this.changed.event;
  private tree: CloudGroup[] = [];
  // Cache of live node instances keyed by `Node.id`, plus the parent each was last handed
  // out under, so `getParent`/`TreeView.reveal` work without VS Code having walked the tree
  // itself. Cleared whenever the underlying data (store) or session (profile/BU/sign-in)
  // changes, since ids can then point at stale content.
  private nodes = new Map<string, Node>();
  private parents = new Map<string, Node | undefined>();
  constructor(private readonly s: Session) {
    s.store.onDidChange(() => { this.nodes.clear(); this.parents.clear(); this.tree = buildTree(s.store.workspaces); this.changed.fire(undefined); });
    s.onDidChange(() => { this.nodes.clear(); this.parents.clear(); this.changed.fire(undefined); });
  }
  getTreeItem(n: Node) { return n; }

  /** Returns the cached instance for `node.id` (recording `parent` against it either way) so
   *  repeated `getChildren` calls hand back the SAME object VS Code already knows about.
   *  Nodes without an id (MessageNode) are never cached. */
  private remember<T extends Node>(node: T, parent: Node | undefined): T {
    if (!node.id) return node;
    const cached = this.nodes.get(node.id) as T | undefined;
    this.parents.set(node.id, parent);
    if (cached) return cached;
    this.nodes.set(node.id, node);
    return node;
  }

  getChildren(n?: Node): Node[] {
    if (!n) {
      if (!this.s.profile) return [new MessageNode("No profile configured — Settings → Terraducktel", "gear")];
      if (!this.s.tokens?.isSignedIn()) return [];
      const head: Node[] = [];
      if (this.s.store.lastError) head.push(new MessageNode(`Refresh failed: ${this.s.store.lastError.message}`, "warning"));
      if (this.s.profile.insecureTls) head.push(new MessageNode("TLS verification is OFF for this profile", "shield"));
      if (!this.tree.length && !this.s.store.lastError) head.push(new MessageNode(`No workspaces in BU “${this.s.bu || "default"}”`));
      return [...head, ...this.tree.map((g) => this.remember(new CloudNode(g), undefined))];
    }
    if (n instanceof CloudNode) return n.group.regions.map((r) => this.remember(new RegionNode(n.group, r), n));
    if (n instanceof RegionNode) return this.folderChildren(n.region.root, `${n.group.key}/${n.region.region}`, n);
    if (n instanceof FolderTreeNode) return this.folderChildren(n.folder, n.path, n);
    if (n instanceof WorkspaceNode) return this.s.store.runsFor(n.ws.id).slice(0, 10).map((r) => this.remember(new RunNode(r), n));
    return [];
  }
  private folderChildren(f: FolderNode, path: string, parent: Node): Node[] {
    const folders = [...f.folders.values()].map((c) => this.remember(new FolderTreeNode(c, `${path}/${c.name}`), parent));
    const leaves = f.workspaces.map(({ ws, leaf }) => { const runs = this.s.store.runsFor(ws.id); return this.remember(new WorkspaceNode(ws, leaf, runs[0], runs.length), parent); });
    return [...folders, ...leaves];
  }
  getParent(n: Node): Node | undefined { return n.id ? this.parents.get(n.id) : undefined; }

  /** Finds (building + caching as needed) the `WorkspaceNode` for `wsId`, walking the same
   *  cloud → region → folder chain `getChildren` would, through the same `remember` path —
   *  so `revealWorkspace` works even before the user has expanded anything. */
  nodeForWorkspace(wsId: string): WorkspaceNode | undefined {
    const cached = this.nodes.get(`ws:${wsId}`);
    if (cached instanceof WorkspaceNode) return cached;
    for (const g of this.tree) {
      const cloud = this.remember(new CloudNode(g), undefined);
      for (const r of g.regions) {
        const region = this.remember(new RegionNode(g, r), cloud);
        const found = this.findWorkspaceInFolder(r.root, `${g.key}/${r.region}`, region, wsId);
        if (found) return found;
      }
    }
    return undefined;
  }
  private findWorkspaceInFolder(f: FolderNode, path: string, parent: Node, wsId: string): WorkspaceNode | undefined {
    for (const { ws, leaf } of f.workspaces) {
      if (ws.id === wsId) { const runs = this.s.store.runsFor(ws.id); return this.remember(new WorkspaceNode(ws, leaf, runs[0], runs.length), parent); }
    }
    for (const c of f.folders.values()) {
      const folderNode = this.remember(new FolderTreeNode(c, `${path}/${c.name}`), parent);
      const found = this.findWorkspaceInFolder(c, `${path}/${c.name}`, folderNode, wsId);
      if (found) return found;
    }
    return undefined;
  }

  /** No-op when `wsId` isn't known (e.g. a stale/deleted workspace). */
  async revealWorkspace(view: vscode.TreeView<Node>, wsId: string): Promise<void> {
    const n = this.nodeForWorkspace(wsId);
    if (n) await view.reveal(n, { select: true, focus: true, expand: true });
  }
}
