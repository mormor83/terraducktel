import { beforeAll, describe, expect, it } from "vitest";
import * as vscode from "vscode";
import type { Run, Workspace } from "../../src/api/types";
import type { CloudGroup } from "../../src/state/grouping";
import { initBrandIcons } from "../../src/views/brand";
import { CloudNode, RunNode, StepNode, WorkspaceNode, describeRun, runContextValue, workspaceDescription } from "../../src/views/nodes";

const iconFile = (icon: unknown) => (icon as { dark: { path: string } }).dark.path.split("/").pop();
const ws = { id: "w1", name: "vpc", repo_ref: "main", drift_status: "clean", tf_working_dir: "a/vpc", environment: "dev", kind: "terraform", path_status: "ok", tags: {} } as unknown as Workspace;
const run = (p: Partial<Run> = {}): Run => ({ id: "abcdef123456", workspace_id: "w1", command: "apply", status: "failed", ...p });
const group = (cloud: CloudGroup["cloud"]): CloudGroup => ({ cloud, key: "k", label: "l", regions: [], count: 2 });

describe("node rendering helpers", () => {
  it("builds run context values and labels", () => {
    expect(runContextValue({ id: "r", workspace_id: "w", command: "apply", status: "awaiting_approval" })).toBe("run.awaiting_approval");
    expect(describeRun({ id: "abcdef123456", workspace_id: "w", command: "plan", status: "planned", branch: "main", created_at: "2026-09-12T10:00:00Z" })).toMatch(/^plan · planned · main · abcdef12/);
  });
  it("describes a workspace with drift and branch", () => {
    expect(workspaceDescription({ repo_ref: "feat/x", drift_status: "drifted" } as never, { status: "failed" } as never)).toBe("failed · feat/x · drift");
    expect(workspaceDescription({ repo_ref: "main", drift_status: "clean" } as never, undefined)).toBe("no runs · main");
  });
});

describe("brand tree icons", () => {
  beforeAll(() => initBrandIcons(vscode.Uri.file("/ext")));

  it("gives a workspace its last run's status SVG, and the workspace glyph when it has no runs", () => {
    expect(iconFile(new WorkspaceNode(ws, "vpc", run(), 1).iconPath)).toBe("failed-dark.svg");
    expect(iconFile(new WorkspaceNode(ws, "vpc", undefined, 0).iconPath)).toBe("none-dark.svg");
  });

  it("gives run and step rows their status SVG, spinning while in flight", () => {
    expect(iconFile(new RunNode(run({ status: "awaiting_approval" })).iconPath)).toBe("awaiting-dark.svg");
    expect(iconFile(new StepNode(run(), { id: "s", run_id: "r", position: 0, name: "Plan", status: "success" }).iconPath)).toBe("applied-dark.svg");
    expect((new StepNode(run(), { id: "s", run_id: "r", position: 0, name: "Plan", status: "running" }).iconPath as vscode.ThemeIcon).id).toBe("sync~spin");
  });

  it("tints cloud-group icons with the brand accent", () => {
    for (const [cloud, id] of [["aws", "cloud"], ["azure", "azure"], ["gcp", "globe"]] as const) {
      const icon = new CloudNode(group(cloud)).iconPath as vscode.ThemeIcon;
      expect(icon.id).toBe(id);
      expect((icon.color as vscode.ThemeColor).id).toBe("terraducktel.accent");
    }
    expect(new CloudNode(group("aws")).description).toBe("AWS · 2");
  });
});
