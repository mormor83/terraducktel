import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { FakeServer } from "../fake-server";
import { TdtClient } from "../../src/api/client";
import { ApprovalWatcher, type ApprovalNotice, type SeenStore } from "../../src/notifications/approvals";

const tokens = { getAccessToken: async () => "t", refreshAccessToken: async () => "t", signOut: async () => {}, hasCredential: () => true };
const run = (id: string, ws = "w1") => ({ id, workspace_id: ws, command: "apply", status: "awaiting_approval", created_at: "2026-09-12T10:00:00Z" });
function memStore(initial?: Record<string, number>): SeenStore & { value?: Record<string, number> } {
  const s: SeenStore & { value?: Record<string, number> } = { value: initial, get: () => s.value, set: async (v) => { s.value = v; } };
  return s;
}

describe("ApprovalWatcher", () => {
  let srv: FakeServer; let url: string; let client: TdtClient; let notices: ApprovalNotice[]; let now: number;
  beforeEach(async () => { srv = new FakeServer(); url = await srv.start(); client = new TdtClient({ baseUrl: url, bu: "default", tokens }); notices = []; now = 1_000_000; });
  afterEach(async () => { await srv.stop(); });
  const mk = (seen = memStore(), ttlMs?: number) => new ApprovalWatcher({ client: () => client, workspaceName: (id) => (id === "w1" ? "vpc" : id), notify: (n) => notices.push(n), seen, now: () => now, ttlMs });

  it("notifies once per new awaiting run, with the graph summary when available", async () => {
    let awaiting = [run("r1")];
    srv.on("GET", "/api/v1/runs", (_q, _b, res) => { res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify(awaiting)); });
    srv.json("GET", "/api/v1/runs/r1/graph", 200, { nodes: [], edges: [], summary: { add: 1, change: 2, destroy: 0 } });
    const w = mk();
    await w.poll(); await w.poll();
    expect(notices.map((n) => n.run.id)).toEqual(["r1"]);
    expect(notices[0]).toMatchObject({ workspaceName: "vpc", summary: { add: 1, change: 2, destroy: 0 } });
    awaiting = [run("r1"), run("r2", "w2")];
    srv.json("GET", "/api/v1/runs/r2/graph", 500, { detail: "boom" });
    await w.poll();
    expect(notices.map((n) => n.run.id)).toEqual(["r1", "r2"]);
    expect(notices[1].summary).toBeUndefined();
    const q = new URL(srv.requests("GET", "/api/v1/runs")[0].url, "http://x").searchParams;
    expect(q.get("status")).toBe("awaiting_approval"); expect(q.get("limit")).toBe("100");
  });

  it("prime() records the current set as seen without notifying", async () => {
    srv.json("GET", "/api/v1/runs", 200, [run("r1"), run("r2")]);
    const seen = memStore(); const w = mk(seen);
    await w.prime(); await w.poll();
    expect(notices).toEqual([]); expect(Object.keys(seen.value ?? {}).sort()).toEqual(["r1", "r2"]);
  });

  it("persists seen ids with timestamps, prunes entries older than the TTL, and honours persisted state across instances", async () => {
    srv.json("GET", "/api/v1/runs", 200, [run("r1")]); srv.json("GET", "/api/v1/runs/r1/graph", 200, { nodes: [], edges: [], summary: {} });
    const seen = memStore({ old: now - 25 * 3_600_000, fresh: now - 3_600_000 });
    await mk(seen, 24 * 3_600_000).poll();
    expect(seen.value).toMatchObject({ r1: now, fresh: now - 3_600_000 }); expect(seen.value?.old).toBeUndefined();
    notices = [];
    await mk(seen).poll();                    // a new instance (window reload) with the same store
    expect(notices).toEqual([]);
  });

  it("is single-flight and silent on failures", async () => {
    let inflight = 0, max = 0;
    srv.on("GET", "/api/v1/runs", (_q, _b, res) => { inflight++; max = Math.max(max, inflight); setTimeout(() => { inflight--; res.writeHead(503, { "content-type": "application/json" }); res.end("{}"); }, 30); });
    const w = mk();
    await Promise.all([w.poll(), w.poll(), w.poll()]);
    expect(max).toBe(1); expect(notices).toEqual([]);
  });

  it("keeps notifying and polling when notify() throws", async () => {
    let awaiting = [run("r1"), run("r2", "w2")];
    srv.on("GET", "/api/v1/runs", (_q, _b, res) => { res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify(awaiting)); });
    srv.json("GET", /^\/api\/v1\/runs\/r\d+\/graph$/, 200, { nodes: [], edges: [], summary: {} });
    const seen = memStore();
    let calls = 0;
    const w = new ApprovalWatcher({
      client: () => client,
      workspaceName: (id) => (id === "w1" ? "vpc" : id),
      notify: (n) => { calls++; if (calls === 1) throw new Error("boom"); notices.push(n); },
      seen, now: () => now,
    });
    await w.poll();
    expect(notices.map((n) => n.run.id)).toEqual(["r2"]);   // r1's notify() threw, r2 still notified
    notices = [];
    awaiting = [run("r1"), run("r2", "w2"), run("r3", "w2")];
    await w.poll();                                          // the next poll() still runs
    expect(notices.map((n) => n.run.id)).toEqual(["r3"]);
  });

  it("does nothing without a client, and start(0) stops the timer", async () => {
    const w = new ApprovalWatcher({ client: () => undefined, workspaceName: (x) => x, notify: (n) => notices.push(n), seen: memStore(), now: () => now });
    await w.poll(); expect(notices).toEqual([]); expect(srv.calls.length).toBe(0);
    w.start(1); w.start(0); await new Promise((r) => setTimeout(r, 20)); expect(srv.calls.length).toBe(0); w.dispose();
  });
});
