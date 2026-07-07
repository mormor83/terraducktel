import { describe, expect, it } from "vitest";
import {
  azureInfo,
  buildFolderTree,
  collectNodeWorkspaces,
  countNodeWorkspaces,
  workspacePathSegments,
} from "./paths";
import type { Workspace } from "./types";

// Minimal Workspace factory — only the fields the path helpers read.
function ws(partial: Partial<Workspace> & { name: string }): Workspace {
  return {
    id: partial.name,
    environment: "dev",
    region: "us-east-1",
    aws_account_id: "000000000000",
    drift_status: "unknown",
    ...partial,
  } as Workspace;
}

describe("azureInfo", () => {
  it("parses guid + region from an azure/subscription path", () => {
    const w = ws({
      name: "resource-group",
      region: "global",
      tf_working_dir: "azure/subscription-da59de93-a478-420e-b3e3-28609e47237b/eastus/resource-group",
    });
    expect(azureInfo(w)).toEqual({
      guid: "da59de93-a478-420e-b3e3-28609e47237b",
      region: "eastus",
    });
  });

  it("returns null for a non-azure path", () => {
    expect(azureInfo(ws({ name: "vpc", tf_working_dir: "account-123/us-east-1/vpc" }))).toBeNull();
  });

  it("returns null when the path is empty", () => {
    expect(azureInfo(ws({ name: "vpc" }))).toBeNull();
  });

  it("returns null when 'azure' is present but the second segment isn't a subscription", () => {
    expect(azureInfo(ws({ name: "x", tf_working_dir: "azure/eastus/x" }))).toBeNull();
  });

  it("falls back to ws.region when the path omits a region segment", () => {
    const w = ws({ name: "rg", region: "westus", tf_working_dir: "azure/subscription-abc" });
    expect(azureInfo(w)).toEqual({ guid: "abc", region: "westus" });
  });
});

describe("workspacePathSegments", () => {
  it("strips account-<id>/<region> for an AWS path", () => {
    const w = ws({
      name: "ms-worker",
      region: "eu-west-1",
      tf_working_dir: "account-123/eu-west-1/cust01/ms-worker",
    });
    expect(workspacePathSegments(w)).toEqual({ folders: ["cust01"], leaf: "ms-worker" });
  });

  it("strips azure/subscription-<guid>/<region> so the leaf sits at the region", () => {
    const w = ws({
      name: "resource-group",
      region: "global",
      tf_working_dir: "azure/subscription-da59de93/eastus/resource-group",
    });
    expect(workspacePathSegments(w)).toEqual({ folders: [], leaf: "resource-group" });
  });

  it("keeps intermediate folders under an azure region", () => {
    const w = ws({
      name: "gpt4",
      region: "global",
      tf_working_dir: "azure/subscription-x/eastus/openai/gpt4",
    });
    expect(workspacePathSegments(w)).toEqual({ folders: ["openai"], leaf: "gpt4" });
  });

  it("falls back to the workspace name for an empty or '.' path", () => {
    expect(workspacePathSegments(ws({ name: "local-ws", tf_working_dir: "" }))).toEqual({
      folders: [],
      leaf: "local-ws",
    });
    expect(workspacePathSegments(ws({ name: "local-ws", tf_working_dir: "." }))).toEqual({
      folders: [],
      leaf: "local-ws",
    });
  });
});

describe("buildFolderTree", () => {
  it("nests workspaces under their folder segments and counts them", () => {
    const list = [
      ws({ name: "a", region: "eu-west-1", tf_working_dir: "account-1/eu-west-1/cust01/a" }),
      ws({ name: "b", region: "eu-west-1", tf_working_dir: "account-1/eu-west-1/cust01/b" }),
      ws({ name: "c", region: "eu-west-1", tf_working_dir: "account-1/eu-west-1/c" }),
    ];
    const root = buildFolderTree(list);
    expect(countNodeWorkspaces(root)).toBe(3);
    expect(collectNodeWorkspaces(root).map((w) => w.name).sort()).toEqual(["a", "b", "c"]);
    // cust01 folder holds a + b; c sits at the root.
    expect(countNodeWorkspaces(root.folders.get("cust01")!)).toBe(2);
    expect(root.workspaces.map((x) => x.leaf)).toEqual(["c"]);
  });

  it("folds a bare workspace into a sibling folder of the same name", () => {
    // internal-tools (workspace) + internal-tools/backup-agent (workspace)
    const list = [
      ws({ name: "internal-tools", region: "us-east-1", tf_working_dir: "account-1/us-east-1/internal-tools" }),
      ws({ name: "prism", region: "us-east-1", tf_working_dir: "account-1/us-east-1/internal-tools/backup-agent" }),
    ];
    const root = buildFolderTree(list);
    // Both land inside the internal-tools folder, not as folder + lookalike sibling.
    expect(root.workspaces.length).toBe(0);
    const folder = root.folders.get("internal-tools")!;
    expect(folder.workspaces.map((x) => x.leaf).sort()).toEqual(["backup-agent", "internal-tools"]);
  });
});
