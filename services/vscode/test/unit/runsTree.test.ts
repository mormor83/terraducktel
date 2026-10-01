import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { FakeServer } from "../fake-server";
import { TdtClient } from "../../src/api/client";
import type { Run, Workspace } from "../../src/api/types";
import type { Session } from "../../src/session";
import { RunsTree, awaitingBadge } from "../../src/views/runsTree";
import { BuNode, MessageNode, RunNode, StepNode, type Node } from "../../src/views/nodes";

const tokens = { getAccessToken: async () => "t", refreshAccessToken: async () => "t", hasCredential: () => true, signOut: async () => {} };
const run = (p: Partial<Run>): Run => ({ id: "r", workspace_id: "w1", command: "plan", status: "planned", created_at: "2026-01-01", ...p });

const BU = (slug: string, name = slug) => ({ id: `id-${slug}`, slug, name });
type BuRuns = { slug: string; name?: string; runs: Run[]; error?: string };
/** Just enough Session for the tree: per-BU store data, a client, and a signed-in token manager. */
function session(bus: BuRuns[] | Run[], client: TdtClient | undefined): Session {
  const list: BuRuns[] = bus.length && "slug" in bus[0] ? (bus as BuRuns[]) : [{ slug: "default", runs: bus as Run[] }];
  const data = new Map(list.map((b) => [b.slug, { bu: BU(b.slug, b.name), workspaces: [{ id: "w1", name: "vpc" } as Workspace], runs: b.runs, error: b.error, loaded: true }]));
  const store = {
    data, bus: list.map((b) => BU(b.slug, b.name)), visibleBus: () => list.map((b) => BU(b.slug, b.name)),
    get runs() { return list.flatMap((b) => b.runs); },
    workspace: (id: string) => (id === "w1" ? ({ id: "w1", name: "vpc" } as Workspace) : undefined),
    onDidChange: () => ({ dispose() {} }),
  };
  return { store, client, tokens: { isSignedIn: () => true }, onDidChange: () => ({ dispose() {} }) } as unknown as Session;
}
const kids = async (t: RunsTree, n?: Node) => (await t.getChildren(n)) as Node[];

describe("RunsTree", () => {
  let srv: FakeServer; let url: string; let client: TdtClient;
  beforeEach(async () => { srv = new FakeServer(); url = await srv.start(); client = new TdtClient({ baseUrl: url, bu: "b", tokens }); });
  afterEach(async () => { await srv.stop(); });

  it("shows 'No business units' when signed in and the server returned none", async () => {
    const s = { store: { data: new Map(), bus: [], visibleBus: () => [], runs: [], onDidChange: () => ({ dispose() {} }) }, client, tokens: { isSignedIn: () => true }, onDidChange: () => ({ dispose() {} }) } as unknown as Session;
    const roots = await kids(new RunsTree(s));
    expect(roots).toHaveLength(1); expect(roots[0]).toBeInstanceOf(MessageNode); expect(roots[0].label).toBe("No business units");
  });

  it("sorts by status, newest first, and puts unknown statuses last", async () => {
    const runs = [
      run({ id: "unknown", status: "quantum_superposition" }),
      run({ id: "cancelled", status: "cancelled" }),
      run({ id: "gate", status: "awaiting_approval" }),
      run({ id: "old-gate", status: "awaiting_approval", created_at: "2025-01-01" }),
    ];
    const t = new RunsTree(session(runs, client)); const nodes = await kids(t, (await kids(t))[0]);
    expect(nodes.map((n) => (n as RunNode).run.id)).toEqual(["gate", "old-gate", "cancelled", "unknown"]);
  });

  it("renders each run collapsible and lazily fetches its steps as children", async () => {
    srv.json("GET", "/api/v1/runs/r1/steps", 200, [
      { position: 1, name: "Plan", status: "success", duration_seconds: 12 },
      { position: 0, name: "Init", status: "success" },
    ]);
    const tree = new RunsTree(session([run({ id: "r1" })], client));
    const [node] = (await kids(tree, (await kids(tree))[0])) as RunNode[];
    expect(node.collapsibleState).toBe(1);          // Collapsed
    expect(srv.calls.length).toBe(0);               // nothing fetched until it is expanded

    const steps = (await tree.getChildren(node)) as StepNode[];
    expect(steps.map((s) => s.label)).toEqual(["Init", "Plan"]);
    expect(steps.map((s) => s.description)).toEqual(["", "12s"]);
    expect(srv.calls[0].url).toBe("/api/v1/runs/r1/steps?since=0&include_output=false");

    // A terminal run's steps can never change again → served from the cache on re-expand.
    await tree.getChildren(node);
    expect(srv.requests("GET", "/api/v1/runs/r1/steps").length).toBe(1);
  });

  it("re-fetches the steps of a run that is still live", async () => {
    srv.json("GET", "/api/v1/runs/r1/steps", 200, [{ position: 0, name: "Plan", status: "running" }]);
    const tree = new RunsTree(session([run({ id: "r1", status: "running" })], client));
    const [node] = (await kids(tree, (await kids(tree))[0])) as RunNode[];
    await tree.getChildren(node);
    await tree.getChildren(node);
    expect(srv.requests("GET", "/api/v1/runs/r1/steps").length).toBe(2);
  });

  it("surfaces a step fetch failure as a tree message instead of throwing", async () => {
    srv.json("GET", "/api/v1/runs/r1/steps", 500, { detail: "boom" });
    const tree = new RunsTree(session([run({ id: "r1" })], client));
    const [node] = (await kids(tree, (await kids(tree))[0])) as RunNode[];
    const children = await tree.getChildren(node);
    expect(children[0]).toBeInstanceOf(MessageNode);
    expect((children[0] as MessageNode).label).toMatch(/Steps unavailable/);
  });

  it("lists visible BUs as roots (sorted by name), each with its own runs", async () => {
    const tree = new RunsTree(session([{ slug: "z", name: "Zed", runs: [run({ id: "rz" })] }, { slug: "a", name: "Alpha", runs: [run({ id: "ra1" }), run({ id: "ra2" })] }], client));
    const roots = (await kids(tree)) as BuNode[];
    expect(roots.map((r) => r.label)).toEqual(["Alpha", "Zed"]); expect(roots.map((r) => r.description)).toEqual(["a · 2 runs", "z · 1 run"]);
    expect(((await kids(tree, roots[1])) as RunNode[]).map((r) => [r.run.id, r.bu])).toEqual([["rz", "z"]]);
    expect(((await kids(tree, roots[0])) as RunNode[]).map((r) => r.bu)).toEqual(["a", "a"]);
  });

  it("says 'No recent runs' under a BU with none, and shows a BU error", async () => {
    const tree = new RunsTree(session([{ slug: "a", runs: [] }, { slug: "b", runs: [], error: "boom" }], client));
    const [a, b] = (await kids(tree)) as BuNode[];
    expect((await kids(tree, a))[0].label).toBe("No recent runs");
    expect(b.description).toBe("b · error"); expect((await kids(tree, b))[0].label).toMatch(/boom/);
  });

  it("expands the BU only when exactly one is visible", async () => {
    const one = (await kids(new RunsTree(session([{ slug: "a", runs: [] }], client)))) as BuNode[];
    expect(one[0].collapsibleState).toBe(2);
    const two = (await kids(new RunsTree(session([{ slug: "a", runs: [] }, { slug: "b", runs: [] }], client)))) as BuNode[];
    expect(two.map((n) => n.collapsibleState)).toEqual([1, 1]);
  });

  it("fetches a run's steps with that run's BU header", async () => {
    srv.json("GET", "/api/v1/runs/r1/steps", 200, []);
    const tree = new RunsTree(session([{ slug: "a", runs: [run({ id: "r1" })] }, { slug: "b", runs: [] }], client));
    const root = (await kids(tree))[0]; const [node] = await kids(tree, root);
    await kids(tree, node);
    expect(srv.requests("GET", "/api/v1/runs/r1/steps")[0].headers["x-business-unit"]).toBe("a");
  });
});

describe("awaitingBadge", () => {
  it("counts runs awaiting approval, and clears when there are none", () => {
    expect(awaitingBadge([run({ status: "awaiting_approval" }), run({ status: "awaiting_approval" }), run({ status: "failed" })])).toEqual({ value: 2, tooltip: "2 awaiting approval" });
    expect(awaitingBadge([run({ status: "applied" })])).toBeUndefined();
  });
});
