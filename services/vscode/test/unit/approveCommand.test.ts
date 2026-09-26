import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as vscodeStub from "./vscode-stub";
import { FakeServer } from "../fake-server";
import { TdtClient } from "../../src/api/client";
import type { Run } from "../../src/api/types";
import type { Session } from "../../src/session";
import { planSummaryText, registerRunCommands } from "../../src/commands/run";
import { RunNode } from "../../src/views/nodes";

const tokens = { getAccessToken: async () => "t", refreshAccessToken: async () => "t", hasCredential: () => true, signOut: async () => {} };
const run: Run = { id: "r1", workspace_id: "w1", command: "apply", status: "awaiting_approval" };

describe("planSummaryText", () => {
  it("spells out every count, omitting replace when it is zero", () => {
    expect(planSummaryText({ add: 2, change: 1, destroy: 2, replace: 1 })).toBe("+2 to add, ~1 to change, -2 to destroy, ±1 to replace");
    expect(planSummaryText({ add: 2, change: 1, destroy: 2, replace: 0 })).toBe("+2 to add, ~1 to change, -2 to destroy");
    expect(planSummaryText({})).toBe("+0 to add, ~0 to change, -0 to destroy");
  });
});

describe("terraducktel.approve", () => {
  let srv: FakeServer; let approve: (arg: unknown) => Promise<void>; let plansOpened: string[];
  beforeEach(async () => {
    srv = new FakeServer();
    const client = new TdtClient({ baseUrl: await srv.start(), bu: "b", tokens });
    srv.json("GET", "/api/v1/runs/r1/graph", 200, { nodes: [], edges: [], summary: { add: 2, change: 1, destroy: 2, replace: 1 } });
    srv.json("POST", "/api/v1/runs/r1/approve", 200, {});
    const handlers = new Map<string, (a: unknown) => Promise<void>>();
    vi.spyOn(vscodeStub.commands, "registerCommand").mockImplementation(((id: string, fn: (a: unknown) => Promise<void>) => { handlers.set(id, fn); return { dispose() {} }; }) as never);
    const session = { requireClient: () => client, store: { runs: [run], workspace: () => ({ name: "worker-pool" }), refresh: async () => {} } } as unknown as Session;
    plansOpened = [];
    registerRunCommands({ subscriptions: [] } as never, session, { watch() {} } as never, { open: async (id: string) => { plansOpened.push(id); } } as never);
    approve = handlers.get("terraducktel.approve")!;
  });
  afterEach(async () => { vi.restoreAllMocks(); await srv.stop(); });

  const answer = (choice: string | undefined) => vi.spyOn(vscodeStub.window as { showInformationMessage: (...a: unknown[]) => Promise<unknown> }, "showInformationMessage").mockResolvedValue(choice);

  it("asks with a modal information dialog: title, full summary as detail, Approve + Show plan", async () => {
    const ask = answer(undefined);
    await approve(new RunNode(run));
    expect(ask).toHaveBeenCalledWith("Approve apply on worker-pool?", { modal: true, detail: "+2 to add, ~1 to change, -2 to destroy, ±1 to replace" }, "Approve", "Show plan");
  });

  it("posts the approval only on an explicit Approve", async () => {
    answer(undefined);
    await approve(new RunNode(run));
    expect(srv.requests("POST", "/api/v1/runs/r1/approve")).toHaveLength(0);
    answer("Approve");
    await approve(new RunNode(run));
    expect(srv.requests("POST", "/api/v1/runs/r1/approve")).toHaveLength(1);
  });

  it("opens the plan instead of approving on Show plan", async () => {
    answer("Show plan");
    await approve(new RunNode(run));
    expect(plansOpened).toEqual(["r1"]);
    expect(srv.requests("POST", "/api/v1/runs/r1/approve")).toHaveLength(0);
  });
});
