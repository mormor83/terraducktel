import { describe, expect, it } from "vitest";
import { WorkspacesTree } from "../../src/views/workspacesTree";
import type { Workspace } from "../../src/api/types";
import { CloudNode, RegionNode, WorkspaceNode } from "../../src/views/nodes";

const ws = (name: string, dir: string): Workspace => ({ id: name, business_unit_id: "bu", name, environment: "dev", region: "eu-west-1", aws_account_id: "1", tf_working_dir: dir, repo_ref: "main", kind: "terraform", tags: {}, drift_status: "unknown", path_status: "ok", state_backend: "s3" });
function fakeSession(list: Workspace[]) {
  const listeners: Array<() => void> = [];
  return {
    profile: { name: "p", url: "http://x" }, tokens: { isSignedIn: () => true }, bu: "default",
    store: { workspaces: list, runsFor: () => [], lastError: undefined, onDidChange: (l: () => void) => { listeners.push(l); return { dispose() {} }; } },
    onDidChange: () => ({ dispose() {} }), fire: () => listeners.forEach((l) => l()),
  } as never;
}

describe("WorkspacesTree parent chain", () => {
  it("returns stable instances and walks parents up to the cloud group", () => {
    const s = fakeSession([ws("vpc", "account-1/eu-west-1/vpc"), ws("app", "account-1/eu-west-1/team/app")]);
    const t = new WorkspacesTree(s); (s as any).fire();
    const roots = t.getChildren(); expect(roots[0]).toBeInstanceOf(CloudNode);
    const region = t.getChildren(roots[0])[0]; expect(region).toBeInstanceOf(RegionNode);
    const kids = t.getChildren(region);
    const app = t.getChildren(kids.find((k) => !(k instanceof WorkspaceNode))!)[0] as WorkspaceNode;
    expect(app.ws.name).toBe("app");
    expect(t.getParent(app)?.id).toBe("folder:1/eu-west-1/team");
    expect(t.getParent(t.getParent(app)!)).toBe(region);
    expect(t.getParent(region)).toBe(roots[0]);
    expect(t.getChildren(roots[0])[0]).toBe(region);                 // same instance on re-query
    expect(t.nodeForWorkspace("app")).toBe(app);
  });
});
