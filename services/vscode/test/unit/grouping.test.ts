import { describe, expect, it } from "vitest";
import { buildTree, classify, workspacePathSegments } from "../../src/state/grouping";
import type { Workspace } from "../../src/api/types";

const ws = (p: Partial<Workspace> & { name: string }): Workspace => ({
  id: p.name, business_unit_id: "bu", environment: "dev", region: "us-east-1", aws_account_id: "000000000000",
  tf_working_dir: "", repo_ref: "main", kind: "terraform", tags: {}, drift_status: "unknown", path_status: "ok", state_backend: "s3", ...p,
});

describe("classify", () => {
  it("aws by account/region", () => {
    expect(classify(ws({ name: "vpc", aws_account_id: "123456789012", region: "eu-west-1", tf_working_dir: "account-123456789012/eu-west-1/vpc" })))
      .toEqual({ cloud: "aws", key: "123456789012", label: "123456789012", region: "eu-west-1" });
  });
  it("azure by explicit link, region from path", () => {
    const w = ws({ name: "net", aws_account_id: "global", region: "global", azure_subscription_id: "pk1", tf_working_dir: "azure/subscription-11111111-1111-1111-1111-111111111111/westeurope/net" });
    expect(classify(w)).toMatchObject({ cloud: "azure", key: "pk1", region: "westeurope" });
  });
  it("azure by path when unlinked", () => {
    const w = ws({ name: "net", aws_account_id: "global", region: "global", tf_working_dir: "azure/subscription-abc/westeurope/net" });
    expect(classify(w)).toMatchObject({ cloud: "azure", key: "guid:abc", label: "subscription-abc", region: "westeurope" });
  });
  it("gcp by path", () => {
    const w = ws({ name: "gke", aws_account_id: "global", region: "global", tf_working_dir: "gcp/project-acme-prod-1234/us-central1/gke" });
    expect(classify(w)).toMatchObject({ cloud: "gcp", key: "pid:acme-prod-1234", region: "us-central1" });
  });
  it("other providers group under their top folder", () => {
    const w = ws({ name: "dns", aws_account_id: "global", region: "global", tf_working_dir: "cloudflare/tenant-home/dns" });
    expect(classify(w)).toMatchObject({ cloud: "other", key: "other:cloudflare", label: "cloudflare", region: "global" });
  });
});

describe("workspacePathSegments", () => {
  it("strips account/region and returns folders + leaf", () => {
    expect(workspacePathSegments(ws({ name: "w", region: "eu-west-1", tf_working_dir: "account-1/eu-west-1/cust01/worker" }))).toEqual({ folders: ["cust01"], leaf: "worker" });
  });
  it("strips azure and gcp prefixes", () => {
    expect(workspacePathSegments(ws({ name: "w", region: "global", tf_working_dir: "azure/subscription-x/westeurope/team/net" }))).toEqual({ folders: ["team"], leaf: "net" });
    expect(workspacePathSegments(ws({ name: "w", region: "global", tf_working_dir: "gcp/project-p/us-central1/gke" }))).toEqual({ folders: [], leaf: "gke" });
  });
  it("falls back to the name when the path is empty", () => {
    expect(workspacePathSegments(ws({ name: "manual", tf_working_dir: "." }))).toEqual({ folders: [], leaf: "manual" });
  });
});

describe("buildTree", () => {
  it("groups cloud → region → folders → leaves, sorted", () => {
    const tree = buildTree([
      ws({ name: "b", aws_account_id: "1", region: "r1", tf_working_dir: "account-1/r1/b" }),
      ws({ name: "a", aws_account_id: "1", region: "r1", tf_working_dir: "account-1/r1/team/a" }),
      ws({ name: "z", aws_account_id: "1", region: "r2", tf_working_dir: "account-1/r2/z" }),
      ws({ name: "net", aws_account_id: "global", region: "global", tf_working_dir: "azure/subscription-s/westeurope/net" }),
    ]);
    expect(tree.map((g) => [g.cloud, g.key])).toEqual([["aws", "1"], ["azure", "guid:s"]]);
    const r1 = tree[0].regions.find((r) => r.region === "r1")!;
    expect([...r1.root.folders.keys()]).toEqual(["team"]);
    expect(r1.root.workspaces.map((w) => w.leaf)).toEqual(["b"]);
    expect(r1.root.folders.get("team")!.workspaces.map((w) => w.leaf)).toEqual(["a"]);
  });
  it("folds a bare workspace into a same-named folder", () => {
    const tree = buildTree([
      ws({ name: "tools", aws_account_id: "1", region: "r", tf_working_dir: "account-1/r/tools" }),
      ws({ name: "agent", aws_account_id: "1", region: "r", tf_working_dir: "account-1/r/tools/agent" }),
    ]);
    const root = tree[0].regions[0].root;
    expect(root.workspaces).toEqual([]);
    expect(root.folders.get("tools")!.workspaces.map((w) => w.leaf).sort()).toEqual(["agent", "tools"]);
  });
});
