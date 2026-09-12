import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, symlinkSync, realpathSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import * as path from "node:path";
import { GitProbe, defaultExec } from "../../src/editor/git";
import { relativeDir } from "../../src/editor/mapping";

const haveGit = (() => { try { execFileSync("git", ["--version"], { stdio: "ignore" }); return true; } catch { return false; } })();
const d = describe.skipIf(!haveGit);

d("GitProbe", () => {
  let root: string; let file: string;
  beforeAll(() => {
    root = mkdtempSync(path.join(tmpdir(), "tdt-git-"));
    const g = (...a: string[]) => execFileSync("git", a, { cwd: root, stdio: "ignore" });
    g("init", "-q", "-b", "feat/x"); g("remote", "add", "origin", "git@github.com:acme/infra.git");
    mkdirSync(path.join(root, "account-1/eu-west-1/vpc"), { recursive: true });
    file = path.join(root, "account-1/eu-west-1/vpc/main.tf"); writeFileSync(file, "# tf\n");
    g("add", "-A"); execFileSync("git", ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], { cwd: root, stdio: "ignore" });
  });
  afterAll(() => rmSync(root, { recursive: true, force: true }));

  it("reports root, origin and branch for a file inside a checkout", async () => {
    const info = await new GitProbe().info(file);
    expect(info?.root && path.resolve(info.root)).toBe(path.resolve(root));   // macOS tmp symlinks
    expect(info?.remoteUrl).toBe("git@github.com:acme/infra.git");
    expect(info?.branch).toBe("feat/x");
  });
  it("returns undefined outside any checkout and never throws", async () => {
    const outside = mkdtempSync(path.join(tmpdir(), "tdt-nogit-"));
    try { expect(await new GitProbe().info(path.join(outside, "x.tf"))).toBeUndefined(); } finally { rmSync(outside, { recursive: true, force: true }); }
  });
  it("caches per root within the TTL and invalidates on demand", async () => {
    let calls = 0;
    const exec = async (cmd: string, args: string[], cwd: string, t: number) => { calls++; return defaultExec(cmd, args, cwd, t); };
    const p = new GitProbe({ ttlMs: 10_000, exec });
    await p.info(file); const first = calls; await p.info(file); expect(calls).toBe(first);
    p.invalidate(); await p.info(file); expect(calls).toBeGreaterThan(first);
  });
  it("treats a missing origin as remoteUrl undefined", async () => {
    execFileSync("git", ["remote", "remove", "origin"], { cwd: root, stdio: "ignore" });
    const info = await new GitProbe().info(file);
    expect(info?.remoteUrl).toBeUndefined(); expect(info?.branch).toBe("feat/x");
  });

  it("mapping still resolves through a symlinked checkout when both sides are realpath'd", async () => {
    // `git rev-parse --show-toplevel` always returns the realpath. A caller that probes with the
    // realpath but maps against the unresolved (symlinked) editor path would get a `git.root` that
    // isn't a prefix of the file path — status.ts's fix is to realpath the file first and use that
    // resolved path for both `git.info()` and `relativeDir()`.
    const linkParent = mkdtempSync(path.join(tmpdir(), "tdt-link-"));
    const link = path.join(linkParent, "checkout");
    symlinkSync(root, link);
    try {
      const linkedFile = path.join(link, "account-1/eu-west-1/vpc/main.tf");
      const info = await new GitProbe().info(realpathSync(linkedFile));
      expect(info?.root).toBe(realpathSync(root));
      expect(relativeDir(info!.root, realpathSync(linkedFile))).toBe("account-1/eu-west-1/vpc");
      // Using the UNRESOLVED editor path against the resolved root reproduces the bug: no prefix relation.
      expect(relativeDir(info!.root, linkedFile)).toBeUndefined();
    } finally { rmSync(linkParent, { recursive: true, force: true }); }
  });
});
