import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeServer } from "../fake-server";
import { TdtClient } from "../../src/api/client";
import { Store } from "../../src/state/store";

const tokens = { getAccessToken: async () => "t", refreshAccessToken: async () => "t", signOut: async () => {} };

describe("Store", () => {
  let srv: FakeServer; let url: string;
  beforeEach(async () => { srv = new FakeServer(); url = await srv.start(); });
  afterEach(async () => { await srv.stop(); vi.useRealTimers(); });

  it("refresh loads workspaces + runs and indexes runs by workspace", async () => {
    srv.json("GET", "/api/v1/workspaces", 200, [{ id: "w1", name: "a" }]);
    srv.json("GET", "/api/v1/runs", 200, [{ id: "r2", workspace_id: "w1", status: "planned", created_at: "2026-01-02" }, { id: "r1", workspace_id: "w1", status: "failed", created_at: "2026-01-01" }]);
    const s = new Store(() => new TdtClient({ baseUrl: url, bu: "default", tokens }), { runsLimit: 50 });
    let events = 0; s.onDidChange(() => events++);
    await s.refresh();
    expect(s.workspaces.map((w) => w.id)).toEqual(["w1"]);
    expect(s.runsFor("w1").map((r) => r.id)).toEqual(["r2", "r1"]);
    expect(srv.calls.find((c) => c.url.startsWith("/api/v1/runs"))!.url).toBe("/api/v1/runs?limit=50");
    expect(events).toBe(1); expect(s.lastError).toBeUndefined();
  });

  it("keeps the last good data and counts failures", async () => {
    srv.json("GET", "/api/v1/workspaces", 200, [{ id: "w1", name: "a" }]); srv.json("GET", "/api/v1/runs", 200, []);
    const s = new Store(() => new TdtClient({ baseUrl: url, bu: "default", tokens }), { runsLimit: 50 });
    await s.refresh();
    await srv.stop(); // server gone
    await s.refresh();
    expect(s.workspaces.length).toBe(1); expect(s.consecutiveFailures).toBe(1); expect(s.lastError).toBeDefined();
    srv = new FakeServer(); await srv.start(); // afterEach stops this one
  });

  it("does not overlap polls", async () => {
    let inflight = 0, max = 0;
    srv.on("GET", "/api/v1/workspaces", (_q, _b, res) => { inflight++; max = Math.max(max, inflight); setTimeout(() => { inflight--; res.writeHead(200, { "content-type": "application/json" }); res.end("[]"); }, 50); });
    srv.json("GET", "/api/v1/runs", 200, []);
    const s = new Store(() => new TdtClient({ baseUrl: url, bu: "default", tokens }), { runsLimit: 50 });
    await Promise.all([s.refresh(), s.refresh(), s.refresh()]);
    expect(max).toBe(1);
  });
});
