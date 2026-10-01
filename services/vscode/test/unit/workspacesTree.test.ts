import { describe, expect, it } from "vitest";
import { WorkspacesTree } from "../../src/views/workspacesTree";
import type { BusinessUnit, Run, Workspace } from "../../src/api/types";
import type { BuData } from "../../src/state/store";
import { BuNode, CloudNode, FilterHeaderNode, MessageNode, RegionNode, RunNode, WorkspaceNode } from "../../src/views/nodes";
import { TreeItemCollapsibleState } from "./vscode-stub";

const ws = (name: string, dir: string, bu = "bu"): Workspace => ({ id: name, business_unit_id: bu, name, environment: "dev", region: "eu-west-1", aws_account_id: "1", tf_working_dir: dir, repo_ref: "main", kind: "terraform", tags: {}, drift_status: "unknown", path_status: "ok", state_backend: "s3" });
const BU = (slug: string, name = slug): BusinessUnit => ({ id: `id-${slug}`, slug, name });
const data = (bu: BusinessUnit, workspaces: Workspace[] = [], extra: Partial<BuData> = {}): BuData => ({ bu, workspaces, runs: [], loaded: true, ...extra });

/** `bus` is the full list the server returned; `visible` the data for the BUs the filter lets through. */
function fakeSession(visible: BuData[], opts: { all?: BusinessUnit[]; globalError?: Error; runs?: Run[] } = {}) {
  const listeners: Array<() => void> = [];
  const all = opts.all ?? visible.map((d) => d.bu);
  const store = {
    bus: all, data: new Map(visible.map((d) => [d.bu.slug, d])), globalError: opts.globalError,
    visibleBus: () => visible.map((d) => d.bu),
    findWorkspace: (id: string) => { for (const d of visible) { const w = d.workspaces.find((x) => x.id === id); if (w) return { ws: w, bu: d.bu }; } return undefined; },
    runsFor: (id: string) => (opts.runs ?? []).filter((r) => r.workspace_id === id),
    onDidChange: (l: () => void) => { listeners.push(l); return { dispose() {} }; },
  };
  return { profile: { name: "p", url: "http://x" }, tokens: { isSignedIn: () => true }, store, onDidChange: () => ({ dispose() {} }), fire: () => listeners.forEach((l) => l()) } as never;
}
const make = (s: never) => { const t = new WorkspacesTree(s); (s as any).fire(); return t; };

describe("WorkspacesTree business-unit roots", () => {
  it("lists one node per visible BU sorted by name, with slug · count as description", () => {
    const s = fakeSession([data(BU("zeta", "Zeta Co"), [ws("a", "account-1/eu-west-1/a")]), data(BU("alpha", "Alpha"), [ws("b", "account-1/eu-west-1/b"), ws("c", "account-1/eu-west-1/c")])]);
    const roots = make(s).getChildren() as BuNode[];
    expect(roots.map((r) => r.label)).toEqual(["Alpha", "Zeta Co"]);
    expect(roots.every((r) => r instanceof BuNode)).toBe(true);
    expect(roots.map((r) => r.description)).toEqual(["alpha · 2 workspaces", "zeta · 1 workspace"]);
    expect(roots[0].contextValue).toBe("bu"); expect(roots[0].id).toBe("bu:alpha");
  });

  it("groups each BU's workspaces with the existing cloud/region/folder grouping and stamps the BU slug", () => {
    const s = fakeSession([data(BU("alpha"), [ws("vpc", "account-1/eu-west-1/vpc")]), data(BU("beta"), [ws("db", "account-2/eu-west-1/db")])]);
    const t = make(s); const [alpha, beta] = t.getChildren() as BuNode[];
    const cloud = t.getChildren(alpha)[0] as CloudNode; expect(cloud).toBeInstanceOf(CloudNode); expect(cloud.bu).toBe("alpha");
    const region = t.getChildren(cloud)[0] as RegionNode; expect(region.bu).toBe("alpha");
    const leaf = t.getChildren(region)[0] as WorkspaceNode; expect(leaf.ws.name).toBe("vpc"); expect(leaf.bu).toBe("alpha");
    const betaLeaf = t.getChildren(t.getChildren(t.getChildren(beta)[0])[0]); expect(betaLeaf.map((n) => (n as WorkspaceNode).ws.name)).toEqual(["db"]);   // only beta's workspaces
    expect(cloud.id).not.toBe((t.getChildren(beta)[0] as CloudNode).id);                     // ids are unique across BUs
  });

  it("returns stable instances and walks parents up through the BU node", () => {
    const s = fakeSession([data(BU("alpha"), [ws("vpc", "account-1/eu-west-1/vpc"), ws("app", "account-1/eu-west-1/team/app")])]);
    const t = make(s);
    const roots = t.getChildren(); const bu = roots[0];
    const cloud = t.getChildren(bu)[0]; const region = t.getChildren(cloud)[0];
    const kids = t.getChildren(region);
    const app = t.getChildren(kids.find((k) => !(k instanceof WorkspaceNode))!)[0] as WorkspaceNode;
    expect(app.ws.name).toBe("app");
    expect(t.getParent(app)?.id).toBe("folder:alpha:1/eu-west-1/team");
    expect(t.getParent(t.getParent(app)!)).toBe(region);
    expect(t.getParent(region)).toBe(cloud); expect(t.getParent(cloud)).toBe(bu); expect(t.getParent(bu)).toBeUndefined();
    expect(t.getChildren(bu)[0]).toBe(cloud);
    expect(t.nodeForWorkspace("app")).toBe(app);
  });

  it("nodeForWorkspace finds a workspace in any BU before anything was expanded", () => {
    const s = fakeSession([data(BU("alpha"), [ws("a", "account-1/eu-west-1/a")]), data(BU("beta"), [ws("b", "account-2/eu-west-1/b")])]);
    const n = make(s).nodeForWorkspace("b"); expect(n?.bu).toBe("beta");
    const t = make(s); const found = t.nodeForWorkspace("b")!; expect(t.getParent(t.getParent(t.getParent(found)!)!)).toBeInstanceOf(BuNode);
  });

  it("shows a 'No workspaces' message under an empty BU", () => {
    const s = fakeSession([data(BU("alpha"), [])]); const t = make(s);
    const kids = t.getChildren(t.getChildren()[0]); expect(kids[0]).toBeInstanceOf(MessageNode); expect(kids[0].label).toBe("No workspaces");
  });

  it("shows a BU's error as its description and as a message child, keeping last-good data below it", () => {
    const s = fakeSession([data(BU("alpha"), [ws("a", "account-1/eu-west-1/a")], { error: "boom" })]); const t = make(s);
    const root = t.getChildren()[0] as BuNode; expect(root.description).toBe("alpha · error");
    const kids = t.getChildren(root); expect(kids[0]).toBeInstanceOf(MessageNode); expect(kids[0].label).toMatch(/boom/); expect(kids[1]).toBeInstanceOf(CloudNode);
  });

  it("an errored BU with no data yet shows only the error message", () => {
    const s = fakeSession([data(BU("alpha"), [], { error: "boom", loaded: false })]); const t = make(s);
    const kids = t.getChildren(t.getChildren()[0]); expect(kids.map((k) => k.label)).toEqual(["Refresh failed: boom"]);
  });

  it("puts a 'Showing X of Y — Filter…' row first when some BUs are hidden", () => {
    const s = fakeSession([data(BU("alpha"))], { all: [BU("alpha"), BU("beta"), BU("gamma")] });
    const roots = make(s).getChildren(); const head = roots[0] as FilterHeaderNode;
    expect(head).toBeInstanceOf(FilterHeaderNode); expect(head.label).toBe("Showing 1 of 3 business units — Filter…");
    expect((head.command as { command: string }).command).toBe("terraducktel.filterBusinessUnits");
    expect(roots[1]).toBeInstanceOf(BuNode);
  });

  it("has no filter row when every BU is visible", () => {
    const roots = make(fakeSession([data(BU("alpha")), data(BU("beta"))])).getChildren();
    expect(roots.some((r) => r instanceof FilterHeaderNode)).toBe(false);
  });

  it("expands the BU when exactly one is visible, collapses them otherwise", () => {
    const one = make(fakeSession([data(BU("alpha"))])).getChildren() as BuNode[];
    expect(one[0].collapsibleState).toBe(TreeItemCollapsibleState.Expanded);
    const two = make(fakeSession([data(BU("alpha")), data(BU("beta"))])).getChildren() as BuNode[];
    expect(two.map((n) => n.collapsibleState)).toEqual([TreeItemCollapsibleState.Collapsed, TreeItemCollapsibleState.Collapsed]);
  });

  it("surfaces a BU-list failure at the top while keeping the BU nodes", () => {
    const s = fakeSession([data(BU("alpha"))], { globalError: new Error("down") });
    const roots = make(s).getChildren(); expect(roots[0]).toBeInstanceOf(MessageNode); expect(roots[0].label).toBe("Refresh failed: down"); expect(roots[1]).toBeInstanceOf(BuNode);
  });

  it("shows 'No business units' when signed in with none at all and no error", () => {
    const roots = make(fakeSession([], { all: [] })).getChildren();
    expect(roots).toHaveLength(1); expect(roots[0]).toBeInstanceOf(MessageNode); expect(roots[0].label).toBe("No business units");
  });

  it("still warns that TLS verification is off when there are no business units", () => {
    const s = fakeSession([], { all: [] }); (s as any).profile.insecureTls = true;
    const labels = make(s).getChildren().map((n) => n.label);
    expect(labels).toEqual(["TLS verification is OFF for this profile", "No business units"]);
  });

  it("keeps the 'Showing 0 of N' filter row (not 'No business units') when all are hidden", () => {
    const roots = make(fakeSession([], { all: [BU("alpha")] })).getChildren();
    expect(roots[0]).toBeInstanceOf(FilterHeaderNode); expect(roots.some((n) => n.label === "No business units")).toBe(false);
  });

  it("a workspace's run children carry its BU", () => {
    const run = { id: "r1", workspace_id: "a", command: "plan", status: "planned" } as Run;
    const s = fakeSession([data(BU("alpha"), [ws("a", "account-1/eu-west-1/a")])], { runs: [run] }); const t = make(s);
    const leaf = t.nodeForWorkspace("a")!; const r = t.getChildren(leaf)[0] as RunNode; expect(r.bu).toBe("alpha");
  });
});
