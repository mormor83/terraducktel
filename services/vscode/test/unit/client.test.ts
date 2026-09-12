import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { FakeServer } from "../fake-server";
import { ApiError, TdtClient } from "../../src/api/client";
import type { TokenProvider } from "../../src/api/client";

function tokens(initial = "acc1"): TokenProvider & { access: string; refreshed: number; signedOut: number } {
  const t = {
    access: initial, refreshed: 0, signedOut: 0,
    getAccessToken: async () => t.access,
    refreshAccessToken: async () => { t.refreshed++; t.access = `acc${t.refreshed + 1}`; return t.access; },
    signOut: async () => { t.signedOut++; },
  };
  return t;
}

describe("TdtClient", () => {
  let srv: FakeServer; let url: string;
  beforeEach(async () => { srv = new FakeServer(); url = await srv.start(); });
  afterEach(async () => { await srv.stop(); });

  it("sends bearer + BU headers and parses JSON", async () => {
    srv.json("GET", "/api/v1/workspaces", 200, [{ id: "w1", name: "vpc" }]);
    const c = new TdtClient({ baseUrl: url, bu: "default", tokens: tokens() });
    const ws = await c.listWorkspaces();
    expect(ws[0].id).toBe("w1");
    const call = srv.calls[0];
    expect(call.headers.authorization).toBe("Bearer acc1");
    expect(call.headers["x-business-unit"]).toBe("default");
    expect(call.headers.accept).toContain("application/json");
  });

  it("encodes query params", async () => {
    srv.json("GET", "/api/v1/runs", 200, []);
    const c = new TdtClient({ baseUrl: url, bu: "default", tokens: tokens() });
    await c.listRuns({ limit: 50, status: ["failed", "cancelled"], workspace_id: "w 1" });
    expect(srv.calls[0].url).toBe("/api/v1/runs?limit=50&status=failed%2Ccancelled&workspace_id=w%201");
  });

  it("on 401 refreshes once and retries with the new token", async () => {
    let n = 0;
    srv.on("GET", "/api/v1/workspaces", (req, _b, res) => {
      n++;
      if (req.headers.authorization === "Bearer acc1") { res.writeHead(401, { "content-type": "application/json" }); res.end(JSON.stringify({ detail: "expired" })); return; }
      res.writeHead(200, { "content-type": "application/json" }); res.end("[]");
    });
    const t = tokens(); const c = new TdtClient({ baseUrl: url, bu: "default", tokens: t });
    expect(await c.listWorkspaces()).toEqual([]);
    expect(n).toBe(2); expect(t.refreshed).toBe(1); expect(srv.calls[1].headers.authorization).toBe("Bearer acc2");
  });

  it("a second 401 signs out and throws ApiError(401)", async () => {
    srv.json("GET", "/api/v1/workspaces", 401, { detail: "nope" });
    const t = tokens(); const c = new TdtClient({ baseUrl: url, bu: "default", tokens: t });
    let out = 0; c.onSignedOut(() => out++);
    await expect(c.listWorkspaces()).rejects.toMatchObject({ status: 401 });
    expect(t.refreshed).toBe(1); expect(t.signedOut).toBe(1); expect(out).toBe(1);
    expect(srv.requests("GET", "/api/v1/workspaces").length).toBe(2);
  });

  it("serialises concurrent refreshes (one refresh for N parallel 401s)", async () => {
    srv.on("GET", "/api/v1/workspaces", (req, _b, res) => {
      if (req.headers.authorization === "Bearer acc1") { res.writeHead(401, { "content-type": "application/json" }); res.end("{}"); return; }
      res.writeHead(200, { "content-type": "application/json" }); res.end("[]");
    });
    const t = tokens(); const c = new TdtClient({ baseUrl: url, bu: "default", tokens: t });
    await Promise.all([c.listWorkspaces(), c.listWorkspaces(), c.listWorkspaces()]);
    expect(t.refreshed).toBe(1);
  });

  it("surfaces the API detail string on errors", async () => {
    srv.json("POST", "/api/v1/workspaces/w1/runs", 409, { detail: "workspace is locked" });
    const c = new TdtClient({ baseUrl: url, bu: "default", tokens: tokens() });
    const err = await c.triggerRun("w1", { command: "plan" }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError); expect(err.status).toBe(409); expect(err.message).toBe("workspace is locked");
  });

  it("flattens pydantic 422 detail arrays", async () => {
    srv.json("POST", "/api/v1/auth/token", 422, { detail: [{ loc: ["body", "email"], msg: "field required" }] });
    const c = new TdtClient({ baseUrl: url, bu: "default", tokens: tokens() });
    await expect(c.login("", "x")).rejects.toMatchObject({ message: "email: field required" });
  });

  it("does not send auth headers on public auth endpoints", async () => {
    srv.json("GET", "/api/v1/auth/config", 200, { mode: "local", oidc_enabled: false, cli_loopback: true });
    const c = new TdtClient({ baseUrl: url, bu: "default", tokens: tokens() });
    await c.authConfig();
    expect(srv.calls[0].headers.authorization).toBeUndefined();
  });

  it("builds steps URLs with since/include_output", async () => {
    srv.json("GET", "/api/v1/runs/r1/steps", 200, []);
    const c = new TdtClient({ baseUrl: url, bu: "default", tokens: tokens() });
    await c.getSteps("r1", 3);
    expect(srv.calls[0].url).toBe("/api/v1/runs/r1/steps?since=3");
  });

  it("with slow refresh, concurrent 401s still make exactly one refresh and all succeed", async () => {
    srv.on("GET", "/api/v1/workspaces", (req, _b, res) => {
      if (req.headers.authorization === "Bearer acc1") { res.writeHead(401, { "content-type": "application/json" }); res.end("{}"); return; }
      res.writeHead(200, { "content-type": "application/json" }); res.end("[]");
    });
    const t = {
      access: "acc1", refreshed: 0, signedOut: 0,
      getAccessToken: async () => t.access,
      refreshAccessToken: async () => {
        t.refreshed++;
        // Simulate slow refresh (30ms)
        await new Promise(resolve => setTimeout(resolve, 30));
        t.access = `acc${t.refreshed + 1}`;
        return t.access;
      },
      signOut: async () => { t.signedOut++; },
    };
    const c = new TdtClient({ baseUrl: url, bu: "default", tokens: t });
    const results = await Promise.all([c.listWorkspaces(), c.listWorkspaces(), c.listWorkspaces()]);
    expect(results).toEqual([[], [], []]);
    expect(t.refreshed).toBe(1);
    expect(t.signedOut).toBe(0);
  });

  it("concurrent 401s (even after refresh) sign out exactly once and coalesce listeners", async () => {
    srv.json("GET", "/api/v1/workspaces", 401, { detail: "always unauthorized" });
    const t = tokens();
    const c = new TdtClient({ baseUrl: url, bu: "default", tokens: t });
    let signOutCount = 0;
    c.onSignedOut(() => signOutCount++);
    const results = await Promise.allSettled([c.listWorkspaces(), c.listWorkspaces(), c.listWorkspaces()]);
    expect(results).toEqual([
      { status: "rejected", reason: expect.objectContaining({ status: 401 }) },
      { status: "rejected", reason: expect.objectContaining({ status: 401 }) },
      { status: "rejected", reason: expect.objectContaining({ status: 401 }) },
    ]);
    expect(t.signedOut).toBe(1);
    expect(signOutCount).toBe(1);
    expect(t.refreshed).toBe(1);
  });

  it("sign-out can happen again after re-sign-in on the same client", async () => {
    srv.json("GET", "/api/v1/workspaces", 401, { detail: "always unauthorized" });
    const t = tokens();
    const c = new TdtClient({ baseUrl: url, bu: "default", tokens: t });
    let signOutCount = 0;
    c.onSignedOut(() => signOutCount++);

    // First sign-out burst
    await Promise.allSettled([c.listWorkspaces(), c.listWorkspaces(), c.listWorkspaces()]);
    expect(t.signedOut).toBe(1);
    expect(signOutCount).toBe(1);

    // Simulate re-sign-in by resetting token provider
    t.access = "acc9";
    t.refreshed = 0;
    t.signedOut = 0;

    // Second sign-out burst (server still always returns 401)
    await Promise.allSettled([c.listWorkspaces(), c.listWorkspaces(), c.listWorkspaces()]);
    expect(t.signedOut).toBe(1);
    expect(signOutCount).toBe(2);
  });

  it("a later sign-out still fires while an unrelated request is in flight", async () => {
    srv.json("GET", "/api/v1/workspaces", 401, { detail: "always unauthorized" });
    srv.on("GET", "/api/v1/runs", (_req, _b, res) => {
      setTimeout(() => { res.writeHead(200, { "content-type": "application/json" }); res.end("[]"); }, 600);
    });
    const t = tokens();
    const c = new TdtClient({ baseUrl: url, bu: "default", tokens: t });
    let signOutCount = 0;
    c.onSignedOut(() => signOutCount++);

    // A slow, unrelated authenticated request that stays in flight across both sign-out cycles below.
    const slow = c.listRuns();

    // First sign-out burst.
    await Promise.allSettled([c.listWorkspaces(), c.listWorkspaces(), c.listWorkspaces()]);
    expect(t.signedOut).toBe(1);
    expect(signOutCount).toBe(1);

    // Simulate re-sign-in.
    t.access = "acc9";
    t.refreshed = 0;
    t.signedOut = 0;

    // Second sign-out burst, while `slow` is still pending — must not be blocked by it.
    await Promise.allSettled([c.listWorkspaces(), c.listWorkspaces(), c.listWorkspaces()]);
    expect(t.signedOut).toBe(1);
    expect(signOutCount).toBe(2);

    expect(await slow).toEqual([]);
  });

  it("withBu clones share sign-out coalescing and listeners", async () => {
    srv.json("GET", "/api/v1/workspaces", 401, { detail: "always unauthorized" });
    const t = tokens();
    const c = new TdtClient({ baseUrl: url, bu: "default", tokens: t });
    let signOutCount = 0;
    c.onSignedOut(() => signOutCount++);
    const clone = c.withBu("other");

    const results = await Promise.allSettled([c.listWorkspaces(), clone.listWorkspaces(), c.listWorkspaces()]);
    expect(results).toEqual([
      { status: "rejected", reason: expect.objectContaining({ status: 401 }) },
      { status: "rejected", reason: expect.objectContaining({ status: 401 }) },
      { status: "rejected", reason: expect.objectContaining({ status: 401 }) },
    ]);
    expect(t.signedOut).toBe(1);
    expect(signOutCount).toBe(1);
  });

  it("withBu clones share one in-flight refresh across a parent+clone burst", async () => {
    srv.on("GET", "/api/v1/workspaces", (req, _b, res) => {
      if (req.headers.authorization === "Bearer acc1") { res.writeHead(401, { "content-type": "application/json" }); res.end("{}"); return; }
      res.writeHead(200, { "content-type": "application/json" }); res.end("[]");
    });
    const t = {
      access: "acc1", refreshed: 0, signedOut: 0,
      getAccessToken: async () => t.access,
      refreshAccessToken: async () => {
        t.refreshed++;
        // Simulate slow refresh (30ms)
        await new Promise((resolve) => setTimeout(resolve, 30));
        t.access = `acc${t.refreshed + 1}`;
        return t.access;
      },
      signOut: async () => { t.signedOut++; },
    };
    const c = new TdtClient({ baseUrl: url, bu: "default", tokens: t });
    const clone = c.withBu("other");
    const results = await Promise.all([c.listWorkspaces(), clone.listWorkspaces(), c.listWorkspaces(), clone.listWorkspaces()]);
    expect(results).toEqual([[], [], [], []]);
    expect(t.refreshed).toBe(1);
  });
});
