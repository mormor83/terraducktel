import { describe, expect, it } from "vitest";
import { matchAcrossBus, matchWorkspace, normalizeRepoUrl, relativeDir } from "../../src/editor/mapping";
import type { BusinessUnit, Workspace } from "../../src/api/types";

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
  it("strips ssh's implicit default port (22) so it compares equal to the scp-like form", () => {
    expect(normalizeRepoUrl("ssh://git@host:22/o/r")).toBe(normalizeRepoUrl("git@host:o/r"));
    // A non-default port is a real distinguishing detail and must not be dropped.
    expect(normalizeRepoUrl("ssh://git@host:2222/o/r")).not.toBe(normalizeRepoUrl("git@host:o/r"));
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
  it("accepts a directory literally named '..foo' rather than treating it as 'outside the root'", () => {
    expect(relativeDir("/home/u/infra", "/home/u/infra/..foo/main.tf")).toBe("..foo");
    expect(relativeDir("/home/u/infra", "/home/u/infra/..foo/bar/main.tf")).toBe("..foo/bar");
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
  it("strips a leading './' from tf_working_dir before matching", () => {
    const withDotSlash = [ws({ name: "vpc", tf_working_dir: "./account-1/eu-west-1/vpc" })];
    expect(matchWorkspace(withDotSlash, { relativeDir: "account-1/eu-west-1/vpc", remoteUrl: "https://github.com/acme/infra.git" })).toMatchObject({ ws: { name: "vpc" }, exact: true });
  });
});

describe("matchAcrossBus", () => {
  const bu = (slug: string): BusinessUnit => ({ id: slug, slug, name: slug.toUpperCase() });
  const q = { relativeDir: "account-1/eu-west-1/vpc/modules", remoteUrl: "https://github.com/acme/infra.git" };

  it("finds the single workspace that covers the file, whichever BU it is in", () => {
    const m = matchAcrossBus([{ bu: bu("a"), workspaces: [ws({ name: "x", tf_working_dir: "other/dir" })] }, { bu: bu("b"), workspaces: [ws({ name: "vpc", tf_working_dir: "account-1/eu-west-1/vpc" })] }], q);
    expect(m.map((x) => [x.ws.name, x.bu.slug, x.exact])).toEqual([["vpc", "b", false]]);
  });

  it("returns every candidate when the same path is imported in several BUs", () => {
    const m = matchAcrossBus([{ bu: bu("a"), workspaces: [ws({ name: "vpc", tf_working_dir: "account-1/eu-west-1/vpc" })] }, { bu: bu("b"), workspaces: [ws({ name: "vpc-b", tf_working_dir: "account-1/eu-west-1/vpc" })] }], q);
    expect(m.map((x) => x.bu.slug)).toEqual(["a", "b"]);
  });

  it("returns the best match per BU (not just the longest overall), sorted by BU name", () => {
    const m = matchAcrossBus([{ bu: bu("a"), workspaces: [ws({ name: "vpc", tf_working_dir: "account-1/eu-west-1/vpc" })] }, { bu: bu("b"), workspaces: [ws({ name: "mods", tf_working_dir: "account-1/eu-west-1/vpc/modules" })] }], q);
    expect(m.map((x) => x.ws.name)).toEqual(["vpc", "mods"]);
  });

  it("sorts candidates by BU name", () => {
    const m = matchAcrossBus([{ bu: bu("z"), workspaces: [ws({ name: "zz", tf_working_dir: "account-1/eu-west-1/vpc" })] }, { bu: bu("a"), workspaces: [ws({ name: "aa", tf_working_dir: "account-1/eu-west-1/vpc" })] }], q);
    expect(m.map((x) => x.bu.slug)).toEqual(["a", "z"]);
  });

  it("breaks a BU-name tie by slug, like the JetBrains plugin", () => {
    const same = (slug: string) => ({ id: slug, slug, name: "Platform" });
    const m = matchAcrossBus([{ bu: same("plat-z"), workspaces: [ws({ name: "z", tf_working_dir: "account-1/eu-west-1/vpc" })] }, { bu: same("plat-a"), workspaces: [ws({ name: "a", tf_working_dir: "account-1/eu-west-1/vpc" })] }], q);
    expect(m.map((x) => x.bu.slug)).toEqual(["plat-a", "plat-z"]);
  });

  it("keeps matchWorkspace's rule inside one BU: an unresolvable tie there yields no candidate", () => {
    const tie = [ws({ name: "one", tf_working_dir: "a/b" }), ws({ name: "two", tf_working_dir: "a/b", repo_url: "https://github.com/acme/other.git" })];
    expect(matchAcrossBus([{ bu: bu("a"), workspaces: tie }], { relativeDir: "a/b" })).toEqual([]);
  });

  it("returns nothing when no BU has data", () => { expect(matchAcrossBus([], q)).toEqual([]); });
});
