import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { FakeServer } from "../fake-server";
import { TdtClient } from "../../src/api/client";
import type { Run, Workspace } from "../../src/api/types";
import type { Session } from "../../src/session";
import { RunsTree } from "../../src/views/runsTree";
import { MessageNode, RunNode, StepNode } from "../../src/views/nodes";

const tokens = { getAccessToken: async () => "t", refreshAccessToken: async () => "t", signOut: async () => {} };
const run = (p: Partial<Run>): Run => ({ id: "r", workspace_id: "w1", command: "plan", status: "planned", created_at: "2026-01-01", ...p });

/** Just enough Session for the tree: a store snapshot, a client, and a signed-in token manager. */
function session(runs: Run[], client: TdtClient | undefined): Session {
  const store = {
    runs, workspaces: [{ id: "w1", name: "vpc" } as Workspace],
    workspace: (id: string) => (id === "w1" ? ({ id: "w1", name: "vpc" } as Workspace) : undefined),
    runsFor: () => [],
    onDidChange: () => ({ dispose() {} }),
  };
  return { store, client, tokens: { isSignedIn: () => true }, onDidChange: () => ({ dispose() {} }) } as unknown as Session;
}

describe("RunsTree", () => {
  let srv: FakeServer; let url: string; let client: TdtClient;
  beforeEach(async () => { srv = new FakeServer(); url = await srv.start(); client = new TdtClient({ baseUrl: url, bu: "b", tokens }); });
  afterEach(async () => { await srv.stop(); });

  it("sorts by status, newest first, and puts unknown statuses last", async () => {
    const runs = [
      run({ id: "unknown", status: "quantum_superposition" }),
      run({ id: "cancelled", status: "cancelled" }),
      run({ id: "gate", status: "awaiting_approval" }),
      run({ id: "old-gate", status: "awaiting_approval", created_at: "2025-01-01" }),
    ];
    const nodes = await new RunsTree(session(runs, client)).getChildren();
    expect(nodes.map((n) => (n as RunNode).run.id)).toEqual(["gate", "old-gate", "cancelled", "unknown"]);
  });

  it("renders each run collapsible and lazily fetches its steps as children", async () => {
    srv.json("GET", "/api/v1/runs/r1/steps", 200, [
      { position: 1, name: "Plan", status: "success", duration_seconds: 12 },
      { position: 0, name: "Init", status: "success" },
    ]);
    const tree = new RunsTree(session([run({ id: "r1" })], client));
    const [node] = (await tree.getChildren()) as RunNode[];
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
    const [node] = (await tree.getChildren()) as RunNode[];
    await tree.getChildren(node);
    await tree.getChildren(node);
    expect(srv.requests("GET", "/api/v1/runs/r1/steps").length).toBe(2);
  });

  it("surfaces a step fetch failure as a tree message instead of throwing", async () => {
    srv.json("GET", "/api/v1/runs/r1/steps", 500, { detail: "boom" });
    const tree = new RunsTree(session([run({ id: "r1" })], client));
    const [node] = (await tree.getChildren()) as RunNode[];
    const kids = await tree.getChildren(node);
    expect(kids[0]).toBeInstanceOf(MessageNode);
    expect((kids[0] as MessageNode).label).toMatch(/Steps unavailable/);
  });
});
