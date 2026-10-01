import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as vscodeStub from "./vscode-stub";
import { FakeServer } from "../fake-server";
import { TdtClient } from "../../src/api/client";
import type { Run, Workspace } from "../../src/api/types";
import type { Session } from "../../src/session";
import { registerWorkspaceCommands, runCommandFor } from "../../src/commands/workspace";
import { WorkspaceNode } from "../../src/views/nodes";

const tokens = { getAccessToken: async () => "t", refreshAccessToken: async () => "t", hasCredential: () => true, signOut: async () => {} };
const ws = (id: string, name: string): Workspace => ({ id, business_unit_id: "x", name, environment: "dev", region: "eu-west-1", aws_account_id: "1", tf_working_dir: `a/${name}`, repo_ref: "main", kind: "terraform", tags: {}, drift_status: "unknown", path_status: "ok", state_backend: "s3" });
const RUNS_RE = /^\/api\/v1\/workspaces\/[^/]+\/runs$/;
const W1 = ws("w1", "vpc"), W2 = ws("w2", "db");
const buData = (slug: string, workspaces: Workspace[]) => [slug, { bu: { id: slug, slug, name: slug.toUpperCase() }, workspaces, runs: [], loaded: true }] as const;

describe("workspace commands act in the node's BU", () => {
  let srv: FakeServer; let handlers: Map<string, (a: unknown) => Promise<void>>; let watched: Array<[Run, string]>; let session: Session;
  beforeEach(async () => {
    srv = new FakeServer();
    const client = new TdtClient({ baseUrl: await srv.start(), bu: "", tokens });
    srv.json("POST", RUNS_RE, 200, { id: "run-1", workspace_id: "w1", command: "plan", status: "pending" });
    srv.json("POST", /^\/api\/v1\/workspaces\/[^/]+\/sync$/, 200, {});
    srv.json("GET", /^\/api\/v1\/workspaces\/[^/]+\/branches$/, 200, { source: "git", branches: ["main", "dev"] });
    srv.json("PUT", /^\/api\/v1\/workspaces\/[^/]+$/, 200, {});
    handlers = new Map();
    vi.spyOn(vscodeStub.commands, "registerCommand").mockImplementation(((id: string, fn: (a: unknown) => Promise<void>) => { handlers.set(id, fn); return { dispose() {} }; }) as never);
    session = { requireClient: () => client, clientFor: (bu: string) => client.withBu(bu), store: { data: new Map([buData("alpha", [W1]), buData("beta", [W2])]), refresh: async () => {} } } as unknown as Session;
    watched = [];
    registerWorkspaceCommands({ subscriptions: [] } as never, session, (r, bu) => { watched.push([r, bu]); });
  });
  afterEach(async () => { vi.restoreAllMocks(); await srv.stop(); });

  const node = (w: Workspace, bu: string) => new WorkspaceNode(bu, w, w.name, undefined, 0);
  const header = (method: string, prefix: string) => srv.requests(method, prefix)[0].headers["x-business-unit"];

  it("plan triggers with the node's BU header and hands the BU to the run watcher", async () => {
    await handlers.get("terraducktel.plan")!(node(W2, "beta"));
    expect(header("POST", "/api/v1/workspaces/w2/runs")).toBe("beta");
    expect(watched.map(([r, bu]) => [r.id, bu])).toEqual([["run-1", "beta"]]);
  });

  it("surfaces the server's detail message when plan is forbidden (403)", async () => {
    srv.off("POST", RUNS_RE).json("POST", RUNS_RE, 403, { detail: "Requires operator role in business unit beta" });
    const err = vi.spyOn(vscodeStub.window as { showErrorMessage: (...a: unknown[]) => unknown }, "showErrorMessage").mockResolvedValue(undefined);
    await handlers.get("terraducktel.plan")!(node(W2, "beta"));
    expect(err).toHaveBeenCalledWith("Terraducktel: Requires operator role in business unit beta");
    expect(watched).toEqual([]);
  });

  it("sync and set-branch use the node's BU", async () => {
    await handlers.get("terraducktel.syncWorkspace")!(node(W1, "alpha"));
    expect(header("POST", "/api/v1/workspaces/w1/sync")).toBe("alpha");
    vi.spyOn(vscodeStub.window as { showQuickPick: (...a: unknown[]) => Promise<unknown> }, "showQuickPick").mockResolvedValue({ label: "dev" });
    await handlers.get("terraducktel.setBranch")!(node(W1, "alpha"));
    expect(header("GET", "/api/v1/workspaces/w1/branches")).toBe("alpha");
    expect(header("PUT", "/api/v1/workspaces/w1")).toBe("alpha");
  });

  it("without a node, the picker spans all visible BUs and uses the chosen workspace's BU", async () => {
    const pick = vi.spyOn(vscodeStub.window as { showQuickPick: (...a: unknown[]) => Promise<unknown> }, "showQuickPick").mockImplementation(async (items: unknown) => (items as unknown[])[1]);
    await handlers.get("terraducktel.plan")!(undefined);
    const items = pick.mock.calls[0][0] as Array<{ label: string; detail?: string }>;
    expect(items.map((i) => [i.label, i.detail])).toEqual([["vpc", expect.stringContaining("ALPHA")], ["db", expect.stringContaining("BETA")]]);
    expect(header("POST", "/api/v1/workspaces/w2/runs")).toBe("beta");
  });

  it("runCommandFor binds its client to the given BU", async () => {
    await runCommandFor(session, W1, "alpha", "plan", () => {});
    expect(header("POST", "/api/v1/workspaces/w1/runs")).toBe("alpha");
  });
});
