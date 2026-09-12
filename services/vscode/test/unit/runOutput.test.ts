import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { FakeServer } from "../fake-server";
import { TdtClient } from "../../src/api/client";
import { tailRun } from "../../src/output/runOutput";

const tokens = { getAccessToken: async () => "t", refreshAccessToken: async () => "t", signOut: async () => {} };

describe("tailRun", () => {
  let srv: FakeServer; let url: string;
  beforeEach(async () => { srv = new FakeServer(); url = await srv.start(); });
  afterEach(async () => { await srv.stop(); });

  it("appends only new steps using the since cursor and stops at a terminal status", async () => {
    let poll = 0;
    srv.on("GET", "/api/v1/runs/r1", (_q, _b, res) => { poll++; res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify({ id: "r1", workspace_id: "w", command: "plan", status: poll < 3 ? "running" : "planned" })); });
    srv.on("GET", "/api/v1/runs/r1/steps", (req, _b, res) => {
      const since = Number(new URL(req.url!, "http://x").searchParams.get("since") ?? "0");
      const all = [{ position: 0, name: "Init", status: "success", output: "ok\n" }, { position: 1, name: "Plan", status: poll < 3 ? "running" : "success", output: poll < 3 ? "planning…" : "planning…\nNo changes." }];
      res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify(all.filter((s) => s.position >= since)));
    });
    const lines: string[] = [];
    const final = await tailRun(new TdtClient({ baseUrl: url, bu: "b", tokens }), "r1", { appendLine: (l) => lines.push(l) }, { pollMs: 5 });
    expect(final.status).toBe("planned");
    expect(lines.filter((l) => l.includes("── Init")).length).toBe(1);          // header printed once
    expect(lines.join("\n")).toContain("No changes.");
    expect(lines.filter((l) => l === "ok").length).toBe(1);                      // finished step output not repeated
    const sinces = srv.requests("GET", "/api/v1/runs/r1/steps").map((c) => new URL(c.url, "http://x").searchParams.get("since"));
    expect(sinces[0]).toBe("0"); expect(sinces.slice(1).every((s) => s === "1")).toBe(true); // cursor stays on the unfinished step
  });

  it("honours cancellation", async () => {
    srv.json("GET", "/api/v1/runs/r1", 200, { id: "r1", workspace_id: "w", command: "plan", status: "running" });
    srv.json("GET", "/api/v1/runs/r1/steps", 200, []);
    let cancelled = false; setTimeout(() => (cancelled = true), 30);
    const final = await tailRun(new TdtClient({ baseUrl: url, bu: "b", tokens }), "r1", { appendLine: () => {} }, { pollMs: 5, isCancelled: () => cancelled });
    expect(final.status).toBe("running");
  });

  it("stops polling once cancelled, even when the run never lands", async () => {
    srv.json("GET", "/api/v1/runs/r1", 200, { id: "r1", workspace_id: "w", command: "plan", status: "running" });
    srv.json("GET", "/api/v1/runs/r1/steps", 200, []);
    let cancelled = false;
    setTimeout(() => (cancelled = true), 30);
    const runGets = () => srv.calls.filter((c) => c.method === "GET" && c.url.split("?")[0] === "/api/v1/runs/r1").length;
    const done = tailRun(new TdtClient({ baseUrl: url, bu: "b", tokens }), "r1", { appendLine: () => {} }, { pollMs: 5, isCancelled: () => cancelled });
    await new Promise((r) => setTimeout(r, 60)); // well past the 30ms cancellation
    const countAfterCancel = runGets();
    await new Promise((r) => setTimeout(r, 60)); // no further polling should happen
    expect(runGets()).toBe(countAfterCancel);
    await done;
  });
});
