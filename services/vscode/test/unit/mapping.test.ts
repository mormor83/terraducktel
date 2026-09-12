import { describe, expect, it } from "vitest";
import { matchWorkspace, normalizeRepoUrl, relativeDir } from "../../src/editor/mapping";
import type { Workspace } from "../../src/api/types";

const ws = (p: Partial<Workspace> & { name: string; tf_working_dir: string }): Workspace => ({
  id: p.name, business_unit_id: "bu", environment: "dev", region: "us-east-1", aws_account_id: "1", repo_ref: "main",
  kind: "terraform", tags: {}, drift_status: "unknown", path_status: "ok", state_backend: "s3", repo_url: "https://github.com/acme/infra.git", ...p,
});

describe("normalizeRepoUrl", () => {
  it.each([
    ["https://github.com/Acme/Infra.git", "github.com/acme/infra"],
    ["https://github.com/acme/infra", "github.com/acme/infra"],
    ["git@github.com:acme/infra.git", "github.com/acme/infra"],
    ["ssh://git@github.com/acme/infra.git", "github.com/acme/infra"],
    ["http://forgejo:3002/infra/live/", "forgejo:3002/infra/live"],
    ["https://user:tok@gitlab.example.com/grp/sub/repo.git", "gitlab.example.com/grp/sub/repo"],
    ["local:///mnt/local-repos/probe", "local:/mnt/local-repos/probe"],
    ["git@forgejo.internal:/repos/infra.git", "forgejo.internal/repos/infra"],
  ])("%s → %s", (input, want) => expect(normalizeRepoUrl(input)).toBe(want));
  it("returns undefined for empty / garbage", () => {
    expect(normalizeRepoUrl("")).toBeUndefined(); expect(normalizeRepoUrl(null)).toBeUndefined(); expect(normalizeRepoUrl("not a url")).toBeUndefined();
  });
  it("guards Windows drive paths from scp-URL parsing", () => {
    expect(normalizeRepoUrl("C:\\Users\\x\\repo")).toBeUndefined();
    expect(normalizeRepoUrl("c:/x/repo")).toBeUndefined();
    expect(normalizeRepoUrl("git@github.com:acme/infra.git")).toBe("github.com/acme/infra");
  });
});

describe("relativeDir", () => {
  it("returns the posix directory relative to the root", () => {
    expect(relativeDir("/home/u/infra", "/home/u/infra/account-1/eu-west-1/vpc/main.tf")).toBe("account-1/eu-west-1/vpc");
    expect(relativeDir("/home/u/infra", "/home/u/infra/main.tf")).toBe("");
  });
  it("returns undefined for files outside the root", () => {
    expect(relativeDir("/home/u/infra", "/home/u/other/main.tf")).toBeUndefined();
    expect(relativeDir("/home/u/infra", "/home/u/infra2/main.tf")).toBeUndefined();
  });
});

describe("matchWorkspace", () => {
  const list = [
    ws({ name: "vpc", tf_working_dir: "account-1/eu-west-1/vpc" }),
    ws({ name: "vpc-peering", tf_working_dir: "account-1/eu-west-1/vpc/peering" }),
    ws({ name: "other-repo", tf_working_dir: "account-1/eu-west-1/vpc", repo_url: "https://github.com/acme/other.git" }),
    ws({ name: "local", tf_working_dir: "proxmox/cluster-home/pve/probe", repo_url: "local:///mnt/local-repos/probe" }),
  ];
  it("picks the longest tf_working_dir prefix within the matching repo", () => {
    expect(matchWorkspace(list, { relativeDir: "account-1/eu-west-1/vpc/peering/modules", remoteUrl: "git@github.com:acme/infra.git" })?.ws.name).toBe("vpc-peering");
    expect(matchWorkspace(list, { relativeDir: "account-1/eu-west-1/vpc", remoteUrl: "https://github.com/acme/infra" })).toMatchObject({ ws: { name: "vpc" }, exact: true });
  });
  it("excludes workspaces from a different repo", () => {
    expect(matchWorkspace(list, { relativeDir: "account-1/eu-west-1/vpc", remoteUrl: "https://github.com/acme/other.git" })?.ws.name).toBe("other-repo");
  });
  it("matches local:// workspaces by path alone", () => {
    expect(matchWorkspace(list, { relativeDir: "proxmox/cluster-home/pve/probe" })?.ws.name).toBe("local");
    expect(matchWorkspace(list, { relativeDir: "proxmox/cluster-home/pve/probe", remoteUrl: "https://github.com/acme/infra.git" })?.ws.name).toBe("local");
  });
  it("with an unknown remote falls back to a path match only when unique", () => {
    expect(matchWorkspace(list, { relativeDir: "account-1/eu-west-1/vpc/peering" })?.ws.name).toBe("vpc-peering"); // unique longest
    expect(matchWorkspace(list, { relativeDir: "account-1/eu-west-1/vpc" })).toBeUndefined();               // vpc vs other-repo tie
  });
  it("does not match a parent directory or an unrelated path", () => {
    expect(matchWorkspace(list, { relativeDir: "account-1/eu-west-1", remoteUrl: "https://github.com/acme/infra.git" })).toBeUndefined();
    expect(matchWorkspace(list, { relativeDir: "account-1/eu-west-1/vpcx", remoteUrl: "https://github.com/acme/infra.git" })).toBeUndefined();
  });
});
