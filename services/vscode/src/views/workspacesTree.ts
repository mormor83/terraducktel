import * as vscode from "vscode";
import type { BusinessUnit } from "../api/types";
import type { Session } from "../session";
import { buildTree, type CloudGroup, type FolderNode } from "../state/grouping";
import { BuNode, CloudNode, FilterHeaderNode, FolderTreeNode, MessageNode, RegionNode, RunNode, WorkspaceNode, type Node } from "./nodes";

const byName = (a: BusinessUnit, b: BusinessUnit) => (a.name || a.slug).localeCompare(b.name || b.slug);
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

export class WorkspacesTree implements vscode.TreeDataProvider<Node> {
  private changed = new vscode.EventEmitter<Node | undefined>();
  readonly onDidChangeTreeData = this.changed.event;
  /** Cloud → region → folder grouping per BU slug (computed from that BU's workspaces only). */
  private trees = new Map<string, CloudGroup[]>();
  // Cache of live node instances keyed by `Node.id`, plus the parent each was last handed
  // out under, so `getParent`/`TreeView.reveal` work without VS Code having walked the tree
  // itself. Cleared whenever the underlying data (store) or session (profile/filter/sign-in)
  // changes, since ids can then point at stale content.
  private nodes = new Map<string, Node>();
  private parents = new Map<string, Node | undefined>();
  constructor(private readonly s: Session) {
    s.store.onDidChange(() => { this.nodes.clear(); this.parents.clear(); this.trees = new Map([...s.store.data].map(([slug, d]) => [slug, buildTree(d.workspaces)])); this.changed.fire(undefined); });
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

  private buNode(bu: BusinessUnit): BuNode {
    const d = this.s.store.data.get(bu.slug);
    const desc = d?.error ? `${bu.slug} · error` : `${bu.slug} · ${plural(d?.workspaces.length ?? 0, "workspace")}`;
    const node = new BuNode(bu, desc, this.s.store.visibleBus().length === 1);
    const cached = this.nodes.get(node.id!) as BuNode | undefined;
    if (cached) { cached.description = desc; this.parents.set(node.id!, undefined); return cached; }
    return this.remember(node, undefined);
  }

  getChildren(n?: Node): Node[] {
    const store = this.s.store;
    if (!n) {
      if (!this.s.profile) return [new MessageNode("No profile configured — Settings → Terraducktel", "gear")];
      if (!this.s.tokens?.isSignedIn()) return [];
      const head: Node[] = [];
      if (store.globalError) head.push(new MessageNode(`Refresh failed: ${store.globalError.message}`, "warning"));
      if (this.s.profile.insecureTls) head.push(new MessageNode("TLS verification is OFF for this profile", "shield"));
      if (!store.bus.length && !store.globalError) return [...head, new MessageNode("No business units")];
      const visible = store.visibleBus();
      if (visible.length < store.bus.length) head.push(new FilterHeaderNode(visible.length, store.bus.length));
      return [...head, ...[...visible].sort(byName).map((b) => this.buNode(b))];
    }
    if (n instanceof BuNode) {
      const d = store.data.get(n.bu); const groups = this.trees.get(n.bu) ?? [];
      const msgs: Node[] = [];
      if (d?.error) msgs.push(new MessageNode(`Refresh failed: ${d.error}`, "warning"));
      else if (!groups.length) msgs.push(new MessageNode("No workspaces"));
      return [...msgs, ...groups.map((g) => this.remember(new CloudNode(n.bu, g), n))];
    }
    if (n instanceof CloudNode) return n.group.regions.map((r) => this.remember(new RegionNode(n.bu, n.group, r), n));
    if (n instanceof RegionNode) return this.folderChildren(n.bu, n.region.root, `${n.group.key}/${n.region.region}`, n);
    if (n instanceof FolderTreeNode) return this.folderChildren(n.bu, n.folder, n.path, n);
    if (n instanceof WorkspaceNode) return store.runsFor(n.ws.id).slice(0, 10).map((r) => this.remember(new RunNode(r, { bu: n.bu }), n));
    return [];
  }
  private folderChildren(bu: string, f: FolderNode, path: string, parent: Node): Node[] {
    const folders = [...f.folders.values()].map((c) => this.remember(new FolderTreeNode(bu, c, `${path}/${c.name}`), parent));
    const leaves = f.workspaces.map(({ ws, leaf }) => { const runs = this.s.store.runsFor(ws.id); return this.remember(new WorkspaceNode(bu, ws, leaf, runs[0], runs.length), parent); });
    return [...folders, ...leaves];
  }
  getParent(n: Node): Node | undefined { return n.id ? this.parents.get(n.id) : undefined; }

  /** Finds (building + caching as needed) the `WorkspaceNode` for `wsId`, walking the same
   *  BU → cloud → region → folder chain `getChildren` would, through the same `remember` path —
   *  so `revealWorkspace` works even before the user has expanded anything. */
  nodeForWorkspace(wsId: string): WorkspaceNode | undefined {
    const cached = this.nodes.get(`ws:${wsId}`);
    if (cached instanceof WorkspaceNode) return cached;
    const hit = this.s.store.findWorkspace(wsId); if (!hit) return undefined;
    const slug = hit.bu.slug;
    const buNode = this.buNode(hit.bu);
    for (const g of this.trees.get(slug) ?? []) {
      const cloud = this.remember(new CloudNode(slug, g), buNode);
      for (const r of g.regions) {
        const region = this.remember(new RegionNode(slug, g, r), cloud);
        const found = this.findWorkspaceInFolder(slug, r.root, `${g.key}/${r.region}`, region, wsId);
        if (found) return found;
      }
    }
    return undefined;
  }
  private findWorkspaceInFolder(bu: string, f: FolderNode, path: string, parent: Node, wsId: string): WorkspaceNode | undefined {
    for (const { ws, leaf } of f.workspaces) {
      if (ws.id === wsId) { const runs = this.s.store.runsFor(ws.id); return this.remember(new WorkspaceNode(bu, ws, leaf, runs[0], runs.length), parent); }
    }
    for (const c of f.folders.values()) {
      const folderNode = this.remember(new FolderTreeNode(bu, c, `${path}/${c.name}`), parent);
      const found = this.findWorkspaceInFolder(bu, c, `${path}/${c.name}`, folderNode, wsId);
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
