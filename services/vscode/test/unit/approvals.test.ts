import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { FakeServer } from "../fake-server";
import { TdtClient } from "../../src/api/client";
import { ApprovalWatcher, approvalMessage, type ApprovalNotice, type SeenStore } from "../../src/notifications/approvals";

const tokens = { getAccessToken: async () => "t", refreshAccessToken: async () => "t", signOut: async () => {}, hasCredential: () => true };
const run = (id: string, ws = "w1") => ({ id, workspace_id: ws, command: "apply", status: "awaiting_approval", created_at: "2026-09-12T10:00:00Z" });
function memStore(initial?: Record<string, number>): SeenStore & { value?: Record<string, number> } {
  const s: SeenStore & { value?: Record<string, number> } = { value: initial, get: () => s.value, set: async (v) => { s.value = v; } };
  return s;
}

describe("ApprovalWatcher", () => {
  let srv: FakeServer; let url: string; let client: TdtClient; let notices: ApprovalNotice[]; let now: number;
  beforeEach(async () => { srv = new FakeServer(); srv.json("GET", "/api/v1/business-units", 200, [{ id: "1", slug: "default", name: "Default" }]); url = await srv.start(); client = new TdtClient({ baseUrl: url, bu: "default", tokens }); notices = []; now = 1_000_000; });
  afterEach(async () => { await srv.stop(); hidden = []; });
  let hidden: string[] = [];
  const mk = (seen = memStore(), ttlMs?: number) => new ApprovalWatcher({ client: () => client, hidden: () => hidden, workspaceName: (id) => (id === "w1" ? "vpc" : id), notify: (n) => notices.push(n), seen, now: () => now, ttlMs });

  it("notifies once per new awaiting run, with the graph summary when available", async () => {
    let awaiting: ReturnType<typeof run>[] = [];
    srv.on("GET", "/api/v1/runs", (_q, _b, res) => { res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify(awaiting)); });
    srv.json("GET", "/api/v1/runs/r1/graph", 200, { nodes: [], edges: [], summary: { add: 1, change: 2, destroy: 0 } });
    const w = mk();
    await w.prime();                  // nothing awaiting yet: the watcher is primed and quiet
    awaiting = [run("r1")];
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
    let awaiting: ReturnType<typeof run>[] = [];
    srv.on("GET", "/api/v1/runs", (_q, _b, res) => { res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify(awaiting)); });
    srv.json("GET", "/api/v1/runs/r1/graph", 200, { nodes: [], edges: [], summary: {} });
    const seen = memStore({ old: now - 25 * 3_600_000, fresh: now - 3_600_000 });
    const w1 = mk(seen, 24 * 3_600_000);
    await w1.prime();
    awaiting = [run("r1")];
    await w1.poll();
    expect(notices.map((n) => n.run.id)).toEqual(["r1"]);
    expect(seen.value).toMatchObject({ r1: now, fresh: now - 3_600_000 }); expect(seen.value?.old).toBeUndefined();
    notices = [];
    // A new instance (window reload) with the same store. It primes on an empty backlog, so the
    // only thing that can keep it quiet about r1 below is the persisted entry.
    awaiting = [];
    const w2 = mk(seen);
    await w2.prime();
    awaiting = [run("r1")];
    await w2.poll();
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
    let awaiting: ReturnType<typeof run>[] = [];
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
    await w.prime();
    awaiting = [run("r1"), run("r2", "w2")];
    await w.poll();
    expect(notices.map((n) => n.run.id)).toEqual(["r2"]);   // r1's notify() threw, r2 still notified
    notices = [];
    awaiting = [run("r1"), run("r2", "w2"), run("r3", "w2")];
    await w.poll();                                          // the next poll() still runs
    expect(notices.map((n) => n.run.id)).toEqual(["r3"]);
  });

  // --- timer epoch: stop() must win over an in-flight tick's reschedule ------------------------
  // A slow route (40 ms) so stop()/start() always land while a poll is genuinely in flight.
  const slowRuns = (counter: { n: number }) =>
    srv.on("GET", "/api/v1/runs", (_q, _b, res) => {
      counter.n++;
      setTimeout(() => { res.writeHead(200, { "content-type": "application/json" }); res.end("[]"); }, 40);
    });

  it("stop() during an in-flight poll is not undone by the tick's reschedule", async () => {
    const c = { n: 0 }; slowRuns(c);
    const w = mk();
    w.start(5);
    await new Promise((r) => setTimeout(r, 20));   // first tick fired; its request is in flight
    expect(c.n).toBe(1);
    w.stop();
    await new Promise((r) => setTimeout(r, 60));   // let the in-flight poll land and (not) re-arm
    const after = c.n;
    await new Promise((r) => setTimeout(r, 120));
    expect(c.n).toBe(after);                       // no request after the stop
    w.dispose();
  });

  it("start() twice during an in-flight poll leaves a single loop that stop() kills", async () => {
    const c = { n: 0 }; slowRuns(c);
    const w = mk();
    w.start(5);
    await new Promise((r) => setTimeout(r, 20));   // tick 1 in flight
    w.start(5);                                    // re-arm mid-poll: must not leave two chains
    await new Promise((r) => setTimeout(r, 60));
    w.stop();
    await new Promise((r) => setTimeout(r, 60));
    const after = c.n;
    await new Promise((r) => setTimeout(r, 120));
    expect(c.n).toBe(after);                       // the orphan chain would keep polling every 5 ms
    w.dispose();
  });

  // --- silent until primed ---------------------------------------------------------------------
  it("swallows the backlog on the first poll after prime() failed, then notifies normally", async () => {
    let fail = true;
    let awaiting = [run("r1"), run("r2", "w2")];
    srv.on("GET", "/api/v1/runs", (_q, _b, res) => {
      if (fail) { res.writeHead(503, { "content-type": "application/json" }); res.end("{}"); return; }
      res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify(awaiting));
    });
    srv.json("GET", /^\/api\/v1\/runs\/r\d+\/graph$/, 200, { nodes: [], edges: [], summary: {} });
    const seen = memStore(); const w = mk(seen);
    await w.prime();                                // network down at wake-up
    fail = false;
    await w.poll();                                 // must NOT announce the whole backlog
    expect(notices).toEqual([]);
    expect(Object.keys(seen.value ?? {}).sort()).toEqual(["r1", "r2"]);
    awaiting = [...awaiting, run("r3", "w2")];
    await w.poll();
    expect(notices.map((n) => n.run.id)).toEqual(["r3"]);
  });

  it("a poll whose response beats a slow prime()'s notifies nobody", async () => {
    let req = 0;
    srv.on("GET", "/api/v1/runs", (_q, _b, res) => {
      const delay = req++ === 0 ? 60 : 0;           // the prime (first request) is the slow one
      setTimeout(() => { res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify([run("r1"), run("r2", "w2")])); }, delay);
    });
    srv.json("GET", /^\/api\/v1\/runs\/r\d+\/graph$/, 200, { nodes: [], edges: [], summary: {} });
    const w = mk();
    const priming = w.prime();
    await new Promise((r) => setTimeout(r, 10));
    await w.poll();                                 // resolves first — and must stay silent
    expect(notices).toEqual([]);
    await priming;
    expect(notices).toEqual([]);
  });

  it("a rejecting seen store cannot reject prime(), and leaves it unprimed", async () => {
    srv.json("GET", "/api/v1/runs", 200, [run("r1")]);
    srv.json("GET", /^\/api\/v1\/runs\/r\d+\/graph$/, 200, { nodes: [], edges: [], summary: {} });
    const traces: string[] = [];
    const seen: SeenStore = { get: () => ({}), set: async () => { throw new Error("globalState is toast"); } };
    const w = new ApprovalWatcher({ client: () => client, workspaceName: (x) => x, notify: (n) => notices.push(n), seen, now: () => now, trace: (l) => traces.push(l) });
    await expect(w.prime()).resolves.toBeUndefined();
    expect(traces.some((l) => l.includes("seen.set failed"))).toBe(true);
    await w.poll();                                 // still unprimed: silent, never a spray
    expect(notices).toEqual([]);
  });

  // --- dedupe against the run-output tail toast --------------------------------------------------
  it("markSeen() suppresses the poll notice for a run the run output already announced", async () => {
    let awaiting: ReturnType<typeof run>[] = [];
    srv.on("GET", "/api/v1/runs", (_q, _b, res) => { res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify(awaiting)); });
    srv.json("GET", /^\/api\/v1\/runs\/r\d+\/graph$/, 200, { nodes: [], edges: [], summary: {} });
    const seen = memStore(); const w = mk(seen);
    await w.prime();
    await w.markSeen("r9");
    awaiting = [run("r9"), run("r8", "w2")];
    await w.poll();
    expect(notices.map((n) => n.run.id)).toEqual(["r8"]);   // r9 was announced by the tail toast
    expect(seen.value?.r9).toBe(now);
  });

  it("does nothing without a client, and start(0) stops the timer", async () => {
    const w = new ApprovalWatcher({ client: () => undefined, workspaceName: (x) => x, notify: (n) => notices.push(n), seen: memStore(), now: () => now });
    await w.poll(); expect(notices).toEqual([]); expect(srv.calls.length).toBe(0);
    w.start(1); w.start(0); await new Promise((r) => setTimeout(r, 20)); expect(srv.calls.length).toBe(0); w.dispose();
  });

  describe("across business units", () => {
    const BUS = [{ id: "1", slug: "alpha", name: "Alpha" }, { id: "2", slug: "beta", name: "Beta" }, { id: "3", slug: "gamma", name: "Gamma" }];
    type R = ReturnType<typeof run>[];
    const perBuRuns = (byBu: Record<string, R>) => { srv.off("GET", "/api/v1/runs"); srv.on("GET", "/api/v1/runs", (req, _b, res) => {
      const slug = String(req.headers["x-business-unit"]);
      if (byBu[slug] === undefined) { res.writeHead(500, { "content-type": "application/json" }); res.end(JSON.stringify({ detail: "no" })); return; }
      res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify(byBu[slug]));
    }); };
    const setBus = (bus = BUS) => { srv.off("GET", "/api/v1/business-units"); srv.json("GET", "/api/v1/business-units", 200, bus); };
    const graphs = () => srv.json("GET", /^\/api\/v1\/runs\/[^/]+\/graph$/, 200, { nodes: [], edges: [], summary: {} });

    it("polls every visible BU with its own header and tags each notice with its BU", async () => {
      setBus(); const byBu: Record<string, R> = { alpha: [], beta: [], gamma: [] }; perBuRuns(byBu); graphs();
      const w = mk(); await w.prime();
      byBu.alpha = [run("ra")]; byBu.beta = [run("rb")]; await w.poll();
      expect(notices.map((n) => [n.run.id, n.bu.name])).toEqual([["ra", "Alpha"], ["rb", "Beta"]]);
      const polled = srv.requests("GET", "/api/v1/runs?").map((c) => c.headers["x-business-unit"]).sort();
      expect(polled).toEqual(["alpha", "alpha", "beta", "beta", "gamma", "gamma"]);   // prime + poll, per BU
      expect(srv.requests("GET", "/api/v1/runs/rb/graph")[0].headers["x-business-unit"]).toBe("beta");
    });

    it("polls at most 4 BUs at a time", async () => {
      setBus(["a", "b", "c", "d", "e", "f"].map((slug, i) => ({ id: String(i), slug, name: slug })));
      let inflight = 0, max = 0;
      srv.off("GET", "/api/v1/runs");
      srv.on("GET", "/api/v1/runs", (_q, _b, res) => { inflight++; max = Math.max(max, inflight); setTimeout(() => { inflight--; res.writeHead(200, { "content-type": "application/json" }); res.end("[]"); }, 30); });
      const w = mk(); await w.poll();
      expect(max).toBeGreaterThan(1); expect(max).toBeLessThanOrEqual(4);
    });

    it("does not poll or notify for a hidden BU", async () => {
      setBus(); const byBu: Record<string, R> = { alpha: [], beta: [], gamma: [] }; perBuRuns(byBu); graphs();
      hidden = ["beta"]; const w = mk(); await w.prime();
      byBu.alpha = [run("ra")]; byBu.beta = [run("rb")]; await w.poll();
      expect(notices.map((n) => n.run.id)).toEqual(["ra"]);
      expect(srv.calls.some((c) => c.url.startsWith("/api/v1/runs") && c.headers["x-business-unit"] === "beta")).toBe(false);
    });

    it("swallows a BU's backlog the first time it is polled (e.g. a BU that just became visible)", async () => {
      setBus(BUS.slice(0, 1)); const byBu: Record<string, R> = { alpha: [], beta: [run("old")] }; perBuRuns(byBu); graphs();
      const w = mk(); await w.prime();
      setBus(BUS.slice(0, 2)); byBu.alpha = [run("ra")];
      await w.poll();
      expect(notices.map((n) => n.run.id)).toEqual(["ra"]);        // beta's "old" was recorded, not announced
      byBu.beta = [run("old"), run("new")]; await w.poll();
      expect(notices.map((n) => n.run.id)).toEqual(["ra", "new"]);
    });

    it("one BU failing does not stop the others from notifying", async () => {
      setBus(BUS.slice(0, 2)); perBuRuns({ alpha: [], beta: [] }); graphs();
      const w = mk(); await w.prime();
      perBuRuns({ alpha: [run("ra")] });                           // beta now answers 500
      await w.poll();
      expect(notices.map((n) => n.run.id)).toEqual(["ra"]);
    });
  });

});

describe("approvalMessage", () => {
  const n = (summary?: { add?: number; change?: number; destroy?: number }): ApprovalNotice => ({ run: { id: "r", workspace_id: "w", command: "apply", status: "awaiting_approval" }, workspaceName: "vpc", bu: { id: "1", slug: "plat", name: "Platform" }, summary });
  it("names the workspace and its business unit", () => { expect(approvalMessage(n())).toBe("TDT: vpc (Platform) apply is awaiting approval."); });
  it("appends the plan summary when known", () => { expect(approvalMessage(n({ add: 1, change: 2, destroy: 3 }))).toBe("TDT: vpc (Platform) apply is awaiting approval (+1 ~2 -3)."); });
});
