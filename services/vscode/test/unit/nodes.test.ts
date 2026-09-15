import { describe, expect, it } from "vitest";
import { describeRun, runContextValue, statusIconId, workspaceDescription } from "../../src/views/nodes";

describe("node rendering helpers", () => {
  it("maps statuses to icons", () => {
    expect(statusIconId("applied")).toBe("pass"); expect(statusIconId("failed")).toBe("error");
    expect(statusIconId("awaiting_approval")).toBe("bell"); expect(statusIconId("running")).toBe("sync~spin");
    expect(statusIconId("planned")).toBe("check"); expect(statusIconId("weird")).toBe("circle-outline");
  });
  it("builds run context values and labels", () => {
    expect(runContextValue({ id: "r", workspace_id: "w", command: "apply", status: "awaiting_approval" })).toBe("run.awaiting_approval");
    expect(describeRun({ id: "abcdef123456", workspace_id: "w", command: "plan", status: "planned", branch: "main", created_at: "2026-09-12T10:00:00Z" })).toMatch(/^plan · planned · main · abcdef12/);
  });
  it("describes a workspace with drift and branch", () => {
    expect(workspaceDescription({ repo_ref: "feat/x", drift_status: "drifted" } as never, { status: "failed" } as never)).toBe("failed · feat/x · drift");
    expect(workspaceDescription({ repo_ref: "main", drift_status: "clean" } as never, undefined)).toBe("no runs · main");
  });
});
