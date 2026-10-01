import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeServer } from "../fake-server";
import { TdtClient } from "../../src/api/client";
import { Store } from "../../src/state/store";

const tokens = { getAccessToken: async () => "t", refreshAccessToken: async () => "t", hasCredential: () => true, signOut: async () => {} };

describe("Store", () => {
  let srv: FakeServer; let url: string;
  beforeEach(async () => { srv = new FakeServer(); srv.json("GET", "/api/v1/business-units", 200, [{ id: "id-default", slug: "default", name: "DEFAULT" }]); url = await srv.start(); });
  afterEach(async () => { await srv.stop(); vi.useRealTimers(); });
  const BU = (slug: string) => ({ id: `id-${slug}`, slug, name: slug.toUpperCase() });
  const hdr = (c: { headers: Record<string, unknown> }) => c.headers["x-business-unit"];

  it("refresh loads workspaces + runs and indexes runs by workspace", async () => {
    srv.json("GET", "/api/v1/workspaces", 200, [{ id: "w1", name: "a" }]);
    srv.json("GET", "/api/v1/runs", 200, [{ id: "r2", workspace_id: "w1", status: "planned", created_at: "2026-01-02" }, { id: "r1", workspace_id: "w1", status: "failed", created_at: "2026-01-01" }]);
    const client = new TdtClient({ baseUrl: url, bu: "default", tokens });
    const s = new Store(() => client, () => ({ runsLimit: 50 }));
    let events = 0; s.onDidChange(() => events++);
    await s.refresh();
    expect(s.workspaces.map((w) => w.id)).toEqual(["w1"]);
    expect(s.runsFor("w1").map((r) => r.id)).toEqual(["r2", "r1"]);
    expect(srv.calls.find((c) => c.url.startsWith("/api/v1/runs"))!.url).toBe("/api/v1/runs?limit=50");
    expect(events).toBe(1); expect(s.globalError).toBeUndefined();
  });

  it("keeps the last good data and counts failures", async () => {
    srv.json("GET", "/api/v1/workspaces", 200, [{ id: "w1", name: "a" }]); srv.json("GET", "/api/v1/runs", 200, []);
    const client = new TdtClient({ baseUrl: url, bu: "default", tokens });
    const s = new Store(() => client, () => ({ runsLimit: 50 }));
    await s.refresh();
    await srv.stop(); // server gone
    await s.refresh();
    expect(s.workspaces.length).toBe(1); expect(s.consecutiveFailures).toBe(1); expect(s.globalError).toBeDefined();
    srv = new FakeServer(); await srv.start(); // afterEach stops this one
  });

  it("does not overlap polls", async () => {
    let inflight = 0, max = 0;
    srv.on("GET", "/api/v1/workspaces", (_q, _b, res) => { inflight++; max = Math.max(max, inflight); setTimeout(() => { inflight--; res.writeHead(200, { "content-type": "application/json" }); res.end("[]"); }, 50); });
    srv.json("GET", "/api/v1/runs", 200, []);
    const client = new TdtClient({ baseUrl: url, bu: "default", tokens });
    const s = new Store(() => client, () => ({ runsLimit: 50 }));
    await Promise.all([s.refresh(), s.refresh(), s.refresh()]);
    expect(max).toBe(1);
  });

  it("discards a stale in-flight fetch if the client swaps mid-refresh, and retries with the new one", async () => {
    const srvB = new FakeServer(); srvB.json("GET", "/api/v1/business-units", 200, [{ id: "id-default", slug: "default", name: "DEFAULT" }]); const urlB = await srvB.start();
    srv.on("GET", "/api/v1/workspaces", (_q, _b, res) => {
      setTimeout(() => { res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify([{ id: "a", name: "a" }])); }, 50);
    });
    srv.json("GET", "/api/v1/runs", 200, []);
    srvB.json("GET", "/api/v1/workspaces", 200, [{ id: "b", name: "b" }]);
    srvB.json("GET", "/api/v1/runs", 200, []);
    const clientA = new TdtClient({ baseUrl: url, bu: "default", tokens });
    const clientB = new TdtClient({ baseUrl: urlB, bu: "default", tokens });
    let current: TdtClient | undefined = clientA;
    const s = new Store(() => current, () => ({ runsLimit: 50 }));
    const pending = s.refresh();
    current = clientB; // swap while A's request is still in flight
    await pending;
    expect(s.workspaces.map((w) => w.id)).toEqual(["b"]);
    await srvB.stop();
  });

  it("makes no request and clears the snapshot when the client getter returns undefined", async () => {
    srv.json("GET", "/api/v1/workspaces", 200, [{ id: "w1", name: "a" }]);
    srv.json("GET", "/api/v1/runs", 200, [{ id: "r1", workspace_id: "w1", status: "planned" }]);
    const client = new TdtClient({ baseUrl: url, bu: "default", tokens });
    let signedIn = true;
    const s = new Store(() => (signedIn ? client : undefined), () => ({ runsLimit: 50 }));
    await s.refresh();
    expect(s.workspaces.length).toBe(1);
    const before = srv.calls.length;

    signedIn = false;                       // e.g. the session expired
    await s.refresh();
    expect(s.workspaces).toEqual([]); expect(s.runs).toEqual([]); expect(s.runsFor("w1")).toEqual([]);
    expect(srv.calls.length).toBe(before);  // nothing was sent without a credential
  });

  it("the timer skips the network while inactive and polls again once active", async () => {
    srv.json("GET", "/api/v1/workspaces", 200, []);
    srv.json("GET", "/api/v1/runs", 200, []);
    const client = new TdtClient({ baseUrl: url, bu: "default", tokens });
    const s = new Store(() => client, () => ({ runsLimit: 50 }));
    s.setActive(false);
    s.start(10);
    await new Promise((r) => setTimeout(r, 60));
    expect(srv.calls.length).toBe(0);
    await s.refresh();                       // manual refresh is never gated by visibility
    expect(srv.calls.length).toBe(3);
    s.setActive(true);
    await new Promise((r) => setTimeout(r, 60));
    s.stop();
    expect(srv.calls.length).toBeGreaterThan(2);
  });

  it("stop() during an in-flight tick is not undone by the tick's reschedule", async () => {
    let reqs = 0;
    srv.on("GET", "/api/v1/workspaces", (_q, _b, res) => {
      reqs++;
      setTimeout(() => { res.writeHead(200, { "content-type": "application/json" }); res.end("[]"); }, 40);
    });
    srv.json("GET", "/api/v1/runs", 200, []);
    const client = new TdtClient({ baseUrl: url, bu: "default", tokens });
    const s = new Store(() => client, () => ({ runsLimit: 50 }));
    s.start(5);
    await new Promise((r) => setTimeout(r, 20));   // first tick fired; its refresh is in flight
    expect(reqs).toBe(1);
    s.stop();
    await new Promise((r) => setTimeout(r, 60));   // let the in-flight refresh land and (not) re-arm
    const after = reqs;
    await new Promise((r) => setTimeout(r, 120));
    expect(reqs).toBe(after);
    s.dispose();
  });

  it("reads runsLimit live from the options getter on each refresh", async () => {
    srv.json("GET", "/api/v1/workspaces", 200, []);
    srv.json("GET", "/api/v1/runs", 200, []);
    let limit = 10;
    const client = new TdtClient({ baseUrl: url, bu: "default", tokens });
    const s = new Store(() => client, () => ({ runsLimit: limit }));
    await s.refresh();
    expect(srv.calls.find((c) => c.url.startsWith("/api/v1/runs"))!.url).toBe("/api/v1/runs?limit=10");
    limit = 200;
    await s.refresh();
    expect(srv.calls.filter((c) => c.url.startsWith("/api/v1/runs")).at(-1)!.url).toBe("/api/v1/runs?limit=200");
  });

  describe("per business unit", () => {
    const wsRoute = (server: FakeServer) => server.on("GET", "/api/v1/workspaces", (req, _b, res) => { const bu = String(req.headers["x-business-unit"]); res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify([{ id: `w-${bu}`, name: `ws-${bu}`, business_unit_id: `id-${bu}` }])); });
    const runRoute = (server: FakeServer) => server.on("GET", "/api/v1/runs", (req, _b, res) => { const bu = String(req.headers["x-business-unit"]); res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify([{ id: `r-${bu}`, workspace_id: `w-${bu}`, status: "planned" }])); });
    const mk = (hidden: string[] = []) => { const client = new TdtClient({ baseUrl: url, bu: "", tokens }); return new Store(() => client, () => ({ runsLimit: 50 }), () => hidden); };
    const useBus = (...slugs: string[]) => { srv.off("GET", "/api/v1/business-units"); srv.json("GET", "/api/v1/business-units", 200, slugs.map(BU)); };

    it("keeps at most 4 HTTP requests in flight across BOTH endpoints", async () => {
      useBus("a", "b", "c", "d", "e", "f");
      let inflight = 0, max = 0;
      const slow = (body: string): Parameters<FakeServer["on"]>[2] => (_q, _b, res) => { inflight++; max = Math.max(max, inflight); setTimeout(() => { inflight--; res.writeHead(200, { "content-type": "application/json" }); res.end(body); }, 30); };
      srv.on("GET", "/api/v1/workspaces", slow("[]")); srv.on("GET", "/api/v1/runs", slow("[]"));
      const s = mk(); await s.refresh();
      expect(s.data.size).toBe(6); expect(max).toBeGreaterThan(1); expect(max).toBeLessThanOrEqual(4);
    });

    it("fetches workspaces and runs once per visible BU with that BU's header", async () => {
      useBus("alpha", "beta"); wsRoute(srv); runRoute(srv);
      const s = mk(); await s.refresh();
      expect(s.visibleBus().map((b) => b.slug)).toEqual(["alpha", "beta"]);
      expect(s.data.get("alpha")!.workspaces.map((w) => w.id)).toEqual(["w-alpha"]);
      expect(s.data.get("beta")!.runs.map((r) => r.id)).toEqual(["r-beta"]);
      expect(s.data.get("beta")!.loaded).toBe(true);
      const calls = srv.calls.filter((c) => c.url.startsWith("/api/v1/workspaces") || c.url.startsWith("/api/v1/runs"));
      expect(calls.map((c) => hdr(c)).sort()).toEqual(["alpha", "alpha", "beta", "beta"]);
      expect(hdr(srv.calls.find((c) => c.url === "/api/v1/business-units")!)).toBeUndefined();
    });

    it("does not fetch hidden BUs and drops their data", async () => {
      useBus("alpha", "beta"); wsRoute(srv); runRoute(srv);
      let hidden: string[] = []; const client = new TdtClient({ baseUrl: url, bu: "", tokens }); const s = new Store(() => client, () => ({ runsLimit: 50 }), () => hidden);
      await s.refresh(); srv.calls.length = 0;
      hidden = ["beta"]; await s.refresh();
      expect(s.visibleBus().map((b) => b.slug)).toEqual(["alpha"]);
      expect(s.bus.map((b) => b.slug)).toEqual(["alpha", "beta"]);
      expect(srv.calls.some((c) => hdr(c) === "beta")).toBe(false);
      expect(s.data.has("beta")).toBe(false);
    });

    it("never runs more than 4 BU fetches at once", async () => {
      useBus(...Array.from({ length: 10 }, (_, i) => `b${i}`));
      let cur = 0, max = 0;
      srv.on("GET", "/api/v1/workspaces", (_q, _b, res) => { cur++; max = Math.max(max, cur); setTimeout(() => { cur--; res.writeHead(200, { "content-type": "application/json" }); res.end("[]"); }, 20); });
      runRoute(srv);
      await mk().refresh();
      expect(max).toBeGreaterThan(1); expect(max).toBeLessThanOrEqual(4);
    });

    it("isolates one BU's failure and keeps its last good data", async () => {
      useBus("alpha", "beta"); runRoute(srv);
      let failBeta = false;
      srv.on("GET", "/api/v1/workspaces", (req, _b, res) => {
        const bu = String(req.headers["x-business-unit"]);
        if (bu === "beta" && failBeta) { res.writeHead(500, { "content-type": "application/json" }); res.end(JSON.stringify({ detail: "boom" })); return; }
        res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify([{ id: `w-${bu}`, name: bu }]));
      });
      const s = mk(); await s.refresh();
      failBeta = true; await s.refresh();
      expect(s.data.get("alpha")!.error).toBeUndefined(); expect(s.data.get("alpha")!.workspaces).toHaveLength(1);
      expect(s.data.get("beta")!.error).toMatch(/boom/); expect(s.data.get("beta")!.workspaces.map((w) => w.id)).toEqual(["w-beta"]);
      expect(s.globalError).toBeUndefined();
    });

    it("a failing BU list keeps the previous tree and sets globalError", async () => {
      useBus("alpha"); wsRoute(srv); runRoute(srv);
      const s = mk(); await s.refresh();
      srv.off("GET", "/api/v1/business-units");
      srv.json("GET", "/api/v1/business-units", 500, { detail: "nope" });
      await s.refresh();
      expect(s.globalError?.message).toMatch(/nope/); expect(s.data.get("alpha")!.workspaces).toHaveLength(1); expect(s.consecutiveFailures).toBe(1);
    });

    it("drops a BU that disappears from the list", async () => {
      useBus("alpha", "beta"); wsRoute(srv); runRoute(srv);
      const s = mk(); await s.refresh();
      useBus("alpha"); await s.refresh();
      expect(s.bus.map((b) => b.slug)).toEqual(["alpha"]); expect([...s.data.keys()]).toEqual(["alpha"]);
      expect(s.findWorkspace("w-beta")).toBeUndefined();
    });

    it("findWorkspace / findRun / runsFor look across BUs", async () => {
      useBus("alpha", "beta"); wsRoute(srv); runRoute(srv);
      const s = mk(); await s.refresh();
      expect(s.findWorkspace("w-beta")).toMatchObject({ ws: { id: "w-beta" }, bu: { slug: "beta" } });
      expect(s.findRun("r-alpha")).toMatchObject({ run: { id: "r-alpha" }, bu: { slug: "alpha" } });
      expect(s.runsFor("w-beta").map((r) => r.id)).toEqual(["r-beta"]);
      expect(s.workspaces.map((w) => w.id).sort()).toEqual(["w-alpha", "w-beta"]);
    });

    it("discards data that lands after the filter changed and retries", async () => {
      useBus("alpha", "beta"); runRoute(srv);
      let hidden: string[] = [];
      srv.on("GET", "/api/v1/workspaces", (req, _b, res) => { const bu = String(req.headers["x-business-unit"]); setTimeout(() => { res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify([{ id: `w-${bu}`, name: bu }])); }, 30); });
      const client = new TdtClient({ baseUrl: url, bu: "", tokens }); const s = new Store(() => client, () => ({ runsLimit: 50 }), () => hidden);
      const pending = s.refresh(); await new Promise((r) => setTimeout(r, 10));
      hidden = ["beta"]; await pending;
      expect([...s.data.keys()]).toEqual(["alpha"]);
    });
  });
});
