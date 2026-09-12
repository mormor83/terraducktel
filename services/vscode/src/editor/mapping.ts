import * as path from "node:path";
import type { Workspace } from "../api/types";

/** Canonical "host/path" form for comparing git remotes across schemes. `local://` keeps its path. */
export function normalizeRepoUrl(url: string | null | undefined): string | undefined {
  if (!url) return undefined;
  let u = url.trim();
  if (!u) return undefined;
  if (u.startsWith("local://")) { const p = u.slice("local://".length).replace(/\/+$/, ""); return p ? `local:${p}` : undefined; }
  // scp-like: git@host:org/repo(.git); guard against Windows drive paths (C:\ or c:/)
  // Host must be 2+ chars or contain a dot to avoid matching C:\ or c:/
  const scp = u.match(/^(?:[\w.-]+@)?((?:[\w.-]{2,}|[\w.-]*\.[\w.-]*)):(?!\/\/)([^\s]+)$/);
  if (scp) u = `ssh://${scp[1]}/${scp[2]}`;
  let host: string, p: string;
  // Repo paths are case-insensitive for routing/uniqueness on GitHub, GitLab and Gitea/Forgejo, so compare them case-folded like the host.
  try { const parsed = new URL(u); host = parsed.host.toLowerCase(); p = parsed.pathname.toLowerCase(); } catch { return undefined; }
  if (!host) return undefined;
  p = p.replace(/\/+$/, "").replace(/\.git$/i, "").replace(/^\/+/, "");
  if (!p) return undefined;
  return `${host}/${p}`;
}

/** Directory of `filePath` relative to `gitRoot`, posix-separated; "" at the root; undefined when outside. */
export function relativeDir(gitRoot: string, filePath: string): string | undefined {
  const rel = path.relative(gitRoot, path.dirname(filePath));
  if (rel.startsWith("..") || path.isAbsolute(rel)) return undefined;
  return rel.split(path.sep).join("/");
}

const isPrefix = (dir: string, wd: string) => dir === wd || dir.startsWith(wd.replace(/\/+$/, "") + "/");

/** Longest `tf_working_dir` prefix among workspaces whose repo matches; see Global Constraints for the fallback rules. */
export function matchWorkspace(workspaces: Workspace[], q: { relativeDir: string; remoteUrl?: string }): { ws: Workspace; exact: boolean } | undefined {
  const remote = normalizeRepoUrl(q.remoteUrl);
  const candidates = workspaces.filter((w) => {
    const wd = (w.tf_working_dir ?? "").replace(/^\/+|\/+$/g, "");
    if (!wd || wd === "." || !isPrefix(q.relativeDir, wd)) return false;
    const wsRepo = normalizeRepoUrl(w.repo_url);
    if (wsRepo?.startsWith("local:")) return true;            // local checkouts match by path alone
    if (remote) return wsRepo === remote;                      // known remote: must match
    return true;                                               // unknown remote: path only, resolved below
  });
  if (!candidates.length) return undefined;
  const longest = Math.max(...candidates.map((w) => w.tf_working_dir.length));
  const best = candidates.filter((w) => w.tf_working_dir.length === longest);
  if (best.length !== 1) return undefined;                     // ambiguous (typically unknown remote + same path in two repos)
  const ws = best[0];
  return { ws, exact: ws.tf_working_dir.replace(/^\/+|\/+$/g, "") === q.relativeDir };
}
