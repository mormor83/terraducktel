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
  let handlersRef: Map<string, (a: unknown) => Promise<void>>; let srv: FakeServer; let approve: (arg: unknown) => Promise<void>; let plansOpened: string[];
  beforeEach(async () => {
    srv = new FakeServer();
    const client = new TdtClient({ baseUrl: await srv.start(), bu: "b", tokens });
    srv.json("GET", "/api/v1/runs/r1/graph", 200, { nodes: [], edges: [], summary: { add: 2, change: 1, destroy: 2, replace: 1 } });
    srv.json("POST", "/api/v1/runs/r1/approve", 200, {});
    const handlers = new Map<string, (a: unknown) => Promise<void>>();
    vi.spyOn(vscodeStub.commands, "registerCommand").mockImplementation(((id: string, fn: (a: unknown) => Promise<void>) => { handlers.set(id, fn); return { dispose() {} }; }) as never);
    const buData = (slug: string, runs: Run[]) => [slug, { bu: { id: slug, slug, name: slug.toUpperCase() }, workspaces: [], runs, loaded: true }] as const;
    const session = { requireClient: () => client, clientFor: (bu: string) => client.withBu(bu), store: { runs: [run], data: new Map([buData("alpha", []), buData("beta", [run])]), workspace: () => ({ name: "worker-pool" }), refresh: async () => {} } } as unknown as Session;
    plansOpened = [];
    handlersRef = handlers;
    registerRunCommands({ subscriptions: [] } as never, session, { watch() {} } as never, { open: async (id: string) => { plansOpened.push(id); } } as never);
    approve = handlers.get("terraducktel.approve")!;
  });
  afterEach(async () => { vi.restoreAllMocks(); await srv.stop(); });

  const answer = (choice: string | undefined) => vi.spyOn(vscodeStub.window as { showInformationMessage: (...a: unknown[]) => Promise<unknown> }, "showInformationMessage").mockResolvedValue(choice);

  it("asks with a modal information dialog: title, full summary as detail, Approve + Show plan", async () => {
    const ask = answer(undefined);
    await approve(new RunNode(run, { bu: "beta" }));
    expect(ask).toHaveBeenCalledWith("Approve apply on worker-pool?", { modal: true, detail: "+2 to add, ~1 to change, -2 to destroy, ±1 to replace" }, "Approve", "Show plan");
  });

  it("posts the approval only on an explicit Approve", async () => {
    answer(undefined);
    await approve(new RunNode(run, { bu: "beta" }));
    expect(srv.requests("POST", "/api/v1/runs/r1/approve")).toHaveLength(0);
    answer("Approve");
    await approve(new RunNode(run, { bu: "beta" }));
    expect(srv.requests("POST", "/api/v1/runs/r1/approve")).toHaveLength(1);
  });

  it("sends the node's BU header on the graph read and the approval", async () => {
    answer("Approve");
    await approve(new RunNode(run, { bu: "beta" }));
    expect(srv.requests("GET", "/api/v1/runs/r1/graph")[0].headers["x-business-unit"]).toBe("beta");
    expect(srv.requests("POST", "/api/v1/runs/r1/approve")[0].headers["x-business-unit"]).toBe("beta");
  });

  it("reject and cancel use the node's BU too", async () => {
    srv.json("POST", "/api/v1/runs/r1/reject", 200, {}); srv.json("POST", "/api/v1/runs/r1/cancel", 200, {});
    vi.spyOn(vscodeStub.window as { showInputBox: (...a: unknown[]) => Promise<unknown> }, "showInputBox").mockResolvedValue("nope");
    await handlersRef.get("terraducktel.reject")!(new RunNode(run, { bu: "beta" }));
    await handlersRef.get("terraducktel.cancelRun")!(new RunNode(run, { bu: "alpha" }));
    expect(srv.requests("POST", "/api/v1/runs/r1/reject")[0].headers["x-business-unit"]).toBe("beta");
    expect(srv.requests("POST", "/api/v1/runs/r1/cancel")[0].headers["x-business-unit"]).toBe("alpha");
  });

  it("without a node, picks from all visible BUs and uses the picked run's BU", async () => {
    answer("Approve");
    const pick = vi.spyOn(vscodeStub.window as { showQuickPick: (...a: unknown[]) => Promise<unknown> }, "showQuickPick").mockImplementation(async (items: unknown) => (items as unknown[])[0]);
    await approve(undefined);
    const items = pick.mock.calls[0][0] as Array<{ detail?: string }>;
    expect(items.map((i) => i.detail)).toEqual(["BETA"]);
    expect(srv.requests("POST", "/api/v1/runs/r1/approve")[0].headers["x-business-unit"]).toBe("beta");
  });

  it("opens the plan instead of approving on Show plan", async () => {
    answer("Show plan");
    await approve(new RunNode(run, { bu: "beta" }));
    expect(plansOpened).toEqual(["r1"]);
    expect(srv.requests("POST", "/api/v1/runs/r1/approve")).toHaveLength(0);
  });

  it("surfaces the server's detail message when approve is forbidden (403)", async () => {
    srv.off("POST", "/api/v1/runs/r1/approve").json("POST", "/api/v1/runs/r1/approve", 403, { detail: "Requires operator role in business unit beta" });
    answer("Approve");
    const err = vi.spyOn(vscodeStub.window as { showErrorMessage: (...a: unknown[]) => unknown }, "showErrorMessage").mockResolvedValue(undefined);
    await approve(new RunNode(run, { bu: "beta" }));
    expect(err).toHaveBeenCalledWith("Terraducktel: Requires operator role in business unit beta");
  });
});
