// Pure path tree for the environment-link pickers. No React, no I/O.
//
// Built straight from each workspace's `tf_working_dir` so a node here is
// exactly a path prefix the API pairs on (services/api/app/services/
// env_pairing.py) — the Dashboard tree's grouping (azure/gcp classification,
// folder/leaf collision folding) is deliberately not reused, because a node
// that looks different from the prefix the backend sees would pair
// differently from what the user picked.

import type { EnvLevel } from "../../api/envLinks";
import type { Workspace } from "../workspace-tree/types";

const ACCOUNT_RE = /^account-(\d{6,14})$/;
const REGION_RE =
  /^(us|eu|ap|sa|ca|me|af)-(north|south|east|west|central|northeast|southeast|northwest|southwest)-\d$/;

export type PathNode = {
  name: string;
  path: string;
  level: EnvLevel;
  children: PathNode[];
  /** The workspace whose tf_working_dir is exactly this path, if any. */
  stack: Workspace | null;
  terraformCount: number;
  helmCount: number;
};

export function accountIdOf(path: string): string | null {
  const m = path.split("/", 1)[0].match(ACCOUNT_RE);
  return m ? m[1] : null;
}

function levelOf(segs: string[], isStack: boolean): EnvLevel {
  if (isStack) return "stack";
  if (segs.length === 1 && ACCOUNT_RE.test(segs[0])) return "account";
  if (segs.length === 2 && ACCOUNT_RE.test(segs[0]) && REGION_RE.test(segs[1])) return "region";
  return "folder";
}

export function buildPathTree(workspaces: Workspace[]): PathNode[] {
  type Mut = { name: string; path: string; kids: Map<string, Mut>; stack: Workspace | null };
  const root: Mut = { name: "", path: "", kids: new Map(), stack: null };
  for (const ws of workspaces) {
    const segs = (ws.tf_working_dir ?? "").split("/").filter(Boolean);
    if (segs.length === 0) continue;
    let cur = root;
    for (const seg of segs) {
      let next = cur.kids.get(seg);
      if (!next) {
        next = { name: seg, path: cur.path ? `${cur.path}/${seg}` : seg, kids: new Map(), stack: null };
        cur.kids.set(seg, next);
      }
      cur = next;
    }
    cur.stack = ws;
  }
  const freeze = (m: Mut): PathNode => {
    const children = [...m.kids.values()]
      .sort((a, b) => a.name.localeCompare(b.name))
      .map(freeze);
    const own = m.stack ? (m.stack.kind === "helm" ? [0, 1] : [1, 0]) : [0, 0];
    return {
      name: m.name,
      path: m.path,
      level: levelOf(m.path.split("/"), m.stack !== null),
      children,
      stack: m.stack,
      terraformCount: own[0] + children.reduce((n, c) => n + c.terraformCount, 0),
      helmCount: own[1] + children.reduce((n, c) => n + c.helmCount, 0),
    };
  };
  return [...root.kids.values()].sort((a, b) => a.name.localeCompare(b.name)).map(freeze);
}

/** Keep nodes whose own path/name — or any descendant's — matches `q`. */
export function filterTree(
  nodes: PathNode[],
  q: string,
  extra?: (n: PathNode) => string,
): PathNode[] {
  const needle = q.trim().toLowerCase();
  if (!needle) return nodes;
  const walk = (n: PathNode): PathNode | null => {
    const hay = `${n.path} ${n.stack?.name ?? ""} ${n.stack?.repo_ref ?? ""} ${extra?.(n) ?? ""}`.toLowerCase();
    if (hay.includes(needle)) return n;
    const kids = n.children.map(walk).filter((c): c is PathNode => c !== null);
    return kids.length ? { ...n, children: kids } : null;
  };
  return nodes.map(walk).filter((n): n is PathNode => n !== null);
}

export function findNode(nodes: PathNode[], path: string): PathNode | null {
  for (const n of nodes) {
    if (n.path === path) return n;
    if (path.startsWith(n.path + "/")) return findNode(n.children, path);
  }
  return null;
}
