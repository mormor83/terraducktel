import { execFile } from "node:child_process";
import * as path from "node:path";

export interface GitInfo { root: string; remoteUrl?: string; branch?: string }
export type ExecFn = (cmd: string, args: string[], cwd: string, timeoutMs: number) => Promise<string>;

export const defaultExec: ExecFn = (cmd, args, cwd, timeoutMs) =>
  new Promise((resolve, reject) => {
    execFile(cmd, args, { cwd, timeout: timeoutMs, windowsHide: true, maxBuffer: 1 << 20 }, (err, stdout) => (err ? reject(err) : resolve(String(stdout))));
  });

/** Cheap, cached, never-throwing view of the git checkout a file lives in. */
export class GitProbe {
  private cache = new Map<string, { at: number; info: GitInfo }>();   // by root
  private rootByDir = new Map<string, { at: number; root: string | undefined }>();
  private readonly ttl: number; private readonly timeout: number; private readonly exec: ExecFn;
  constructor(opts: { ttlMs?: number; timeoutMs?: number; exec?: ExecFn } = {}) {
    this.ttl = opts.ttlMs ?? 10_000; this.timeout = opts.timeoutMs ?? 3_000; this.exec = opts.exec ?? defaultExec;
  }
  invalidate(root?: string) {
    if (root) {
      this.cache.delete(root);
      for (const [dir, rc] of this.rootByDir) if (rc.root === root) this.rootByDir.delete(dir);
    } else {
      this.cache.clear();
      this.rootByDir.clear();
    }
  }

  async info(filePath: string): Promise<GitInfo | undefined> {
    const dir = path.dirname(filePath); const now = Date.now();
    let root: string | undefined;
    const rc = this.rootByDir.get(dir);
    if (rc && now - rc.at < this.ttl) root = rc.root;
    else {
      root = await this.exec("git", ["rev-parse", "--show-toplevel"], dir, this.timeout).then((s) => s.trim() || undefined, () => undefined);
      this.rootByDir.set(dir, { at: now, root });
    }
    if (!root) return undefined;
    const hit = this.cache.get(root);
    if (hit && now - hit.at < this.ttl) return hit.info;
    const [remoteUrl, branch] = await Promise.all([
      this.exec("git", ["remote", "get-url", "origin"], root, this.timeout).then((s) => s.trim() || undefined, () => undefined),
      this.exec("git", ["rev-parse", "--abbrev-ref", "HEAD"], root, this.timeout).then((s) => { const b = s.trim(); return b && b !== "HEAD" ? b : undefined; }, () => undefined),
    ]);
    const info: GitInfo = { root, remoteUrl, branch };
    this.cache.set(root, { at: now, info });
    return info;
  }
}
