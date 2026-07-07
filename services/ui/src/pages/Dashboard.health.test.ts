import { describe, expect, it } from "vitest";
import { computeFleetHealth } from "./Dashboard";

const now = Date.now();
const iso = (msAgo: number) => new Date(now - msAgo).toISOString();
const HOUR = 3600 * 1000;

const workspaces = [
  { id: "w1", name: "vpc", environment: "prod", region: "us-east-1", aws_account_id: "1", drift_status: "drifted" },
  { id: "w2", name: "api", environment: "prod", region: "us-east-1", aws_account_id: "1", drift_status: "clean" },
  { id: "w3", name: "dev-x", environment: "dev", region: "us-east-1", aws_account_id: "1", drift_status: "clean" },
] as any;

const runs = [
  // prod: 3 applied, 1 failed (in window) → 75% success
  { id: "r1", workspace_id: "w1", command: "apply", status: "applied", created_at: iso(2 * HOUR), started_at: iso(2 * HOUR), completed_at: iso(2 * HOUR - 120 * 1000) },
  { id: "r2", workspace_id: "w2", command: "apply", status: "applied", created_at: iso(3 * HOUR) },
  { id: "r3", workspace_id: "w2", command: "apply", status: "applied", created_at: iso(4 * HOUR) },
  { id: "r4", workspace_id: "w1", command: "apply", status: "failed", created_at: iso(1 * HOUR) },
  // active (in-flight) — not terminal, not counted in success rate
  { id: "r5", workspace_id: "w3", command: "plan", status: "awaiting_approval", created_at: iso(0.5 * HOUR) },
  { id: "r6", workspace_id: "w3", command: "apply", status: "applying", created_at: iso(0.1 * HOUR) },
  // old failure (outside 7d window) — excluded from rate but still a recent-failure row
  { id: "r7", workspace_id: "w3", command: "apply", status: "failed", created_at: iso(30 * 24 * HOUR) },
] as any;

describe("computeFleetHealth", () => {
  const h = computeFleetHealth(runs, workspaces);

  it("computes 7-day success rate from terminal runs only", () => {
    expect(h.terminal).toBe(4); // 3 applied + 1 failed in window
    expect(h.successRate).toBe(75);
  });

  it("counts active (in-flight) runs", () => {
    expect(h.active).toBe(2); // awaiting_approval + applying
  });

  it("computes avg duration from runs with start+complete", () => {
    expect(h.avgDuration).toBeCloseTo(120, 0); // only r1 has both stamps (120s)
  });

  it("lists recent failures newest-first with workspace context", () => {
    expect(h.recentFailures.map((f) => f.run.id)).toEqual(["r4", "r7"]);
    expect(h.recentFailures[0].ws?.name).toBe("vpc");
  });

  it("aggregates per-environment health", () => {
    const prod = h.byEnv.find((e) => e.env === "prod")!;
    expect(prod.total).toBe(2);
    expect(prod.drifted).toBe(1);
    expect(prod.failures).toBe(1); // r4 in window; r7 (dev, old) excluded
  });

  it("returns null success rate when no terminal runs", () => {
    expect(computeFleetHealth([], workspaces).successRate).toBeNull();
  });
});
