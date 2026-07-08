// Pure path/grouping helpers for the workspace tree. No React, no I/O — these
// are the unit-testable core (see paths.test.ts).

import type { Workspace } from "./types";

/**
 * Detect the Azure layout encoded in a workspace's repo path. The convention
 * mirrors AWS's `account-<id>/<region>/…` as `azure/subscription-<guid>/<region>/…`.
 * Returns the subscription GUID + region when the path matches, else null.
 * This is the *path fallback* for grouping — the explicit
 * `workspace.azure_subscription_id` link wins when present.
 */
export function azureInfo(ws: Workspace): { guid: string; region: string } | null {
  const parts = (ws.tf_working_dir ?? "").trim().split("/").filter(Boolean);
  if (parts[0] !== "azure") return null;
  const m = (parts[1] ?? "").match(/^subscription-(.+)$/);
  if (!m) return null;
  return { guid: m[1], region: parts[2] ?? ws.region };
}

/**
 * Detect the GCP layout encoded in a workspace's repo path. The convention
 * mirrors Azure's as `gcp/project-<project_id>/<region>/…`. Returns the
 * project id + region when the path matches, else null. Path fallback for
 * grouping — the explicit `workspace.gcp_project_id` link wins when present.
 */
export function gcpInfo(ws: Workspace): { projectId: string; region: string } | null {
  const parts = (ws.tf_working_dir ?? "").trim().split("/").filter(Boolean);
  if (parts[0] !== "gcp") return null;
  const m = (parts[1] ?? "").match(/^project-(.+)$/);
  if (!m) return null;
  return { projectId: m[1], region: parts[2] ?? ws.region };
}

/**
 * Split a workspace's `tf_working_dir` into intermediate folder segments + a
 * leaf name. We strip the leading `account-<id>/<region>/` to leave just the
 * path *within* the (account, region) slot. The leaf is what the row should
 * display; the folders are the intermediate groupings rendered above it.
 *
 * Falls back to `workspace.name` if the path is empty / unrecognized — keeps
 * the tree usable for `local://` workspaces and any legacy rows whose
 * `tf_working_dir` doesn't follow the account-XXX/region/... convention.
 */
export function workspacePathSegments(ws: Workspace): { folders: string[]; leaf: string } {
  const raw = (ws.tf_working_dir ?? "").trim();
  if (!raw || raw === ".") return { folders: [], leaf: ws.name };
  const parts = raw.split("/").filter(Boolean);
  if (parts[0]?.startsWith("account-")) parts.shift();
  // Azure: strip the `azure/subscription-<guid>` pair (the subscription is the
  // top-level group) and treat the next segment as the region to strip too, so
  // the leaf sits directly under its region — same shape as an AWS account.
  let regionToStrip = ws.region;
  if (parts[0] === "azure" && /^subscription-/.test(parts[1] ?? "")) {
    parts.shift();
    parts.shift();
    regionToStrip = parts[0] ?? ws.region;
  } else if (parts[0] === "gcp" && /^project-/.test(parts[1] ?? "")) {
    // Strip the `gcp/project-<id>` pair (the project is the top-level group)
    // and treat the next segment as the region to strip — same shape as Azure.
    parts.shift();
    parts.shift();
    regionToStrip = parts[0] ?? ws.region;
  }
  if (parts[0] === regionToStrip) parts.shift();
  if (parts.length === 0) return { folders: [], leaf: ws.name };
  const leaf = parts.pop() as string;
  return { folders: parts, leaf };
}

/**
 * In-memory folder tree built from each workspace's `tf_working_dir`. A repo
 * path like `account-XXX/eu-west-1/cust01/worker-queue-consumer` produces a
 * folder `cust01` containing a workspace whose leaf label is
 * `worker-queue-consumer`. A path with no intermediate segments (e.g.
 * `account-XXX/eu-west-1/internal-tools`) puts the workspace directly at the
 * region level.
 *
 * Folder/workspace name collision rule: when an intermediate folder shares its
 * name with a sibling workspace's leaf (the `internal-tools` case — there's a
 * top-level `internal-tools` workspace AND an `internal-tools/backup-agent`
 * sub-workspace), we fold the bare workspace INTO the folder rather than
 * rendering both at the same level. That's the natural reading of the repo
 * layout.
 */
export type FolderNode = {
  name: string;
  // Iteration order is insertion order; we sort children alphabetically at
  // render time.
  folders: Map<string, FolderNode>;
  workspaces: { ws: Workspace; leaf: string }[];
};

export function buildFolderTree(workspaces: Workspace[]): FolderNode {
  const root: FolderNode = { name: "", folders: new Map(), workspaces: [] };

  // Pass 1: create all intermediate folders. This must complete before we
  // decide where to place each workspace, so the collision-merge rule below
  // can see folders created by other (nested) workspaces.
  const items = workspaces.map((ws) => ({ ws, ...workspacePathSegments(ws) }));
  for (const { folders } of items) {
    let cur = root;
    for (const seg of folders) {
      let next = cur.folders.get(seg);
      if (!next) {
        next = { name: seg, folders: new Map(), workspaces: [] };
        cur.folders.set(seg, next);
      }
      cur = next;
    }
  }

  // Pass 2: place each workspace. If a folder at its parent level already has
  // the same name as this workspace's leaf, file the workspace inside that
  // folder instead of as a sibling — keeps `internal-tools` (workspace) +
  // `internal-tools/backup-agent` (workspace) rendering as one folder with two
  // entries rather than a folder + lookalike sibling.
  for (const { ws, folders, leaf } of items) {
    let cur = root;
    for (const seg of folders) cur = cur.folders.get(seg) as FolderNode;
    const colliding = cur.folders.get(leaf);
    if (colliding) {
      colliding.workspaces.push({ ws, leaf });
    } else {
      cur.workspaces.push({ ws, leaf });
    }
  }

  return root;
}

export function collectNodeWorkspaces(node: FolderNode): Workspace[] {
  const out: Workspace[] = [];
  for (const { ws } of node.workspaces) out.push(ws);
  for (const child of node.folders.values()) out.push(...collectNodeWorkspaces(child));
  return out;
}

export function countNodeWorkspaces(node: FolderNode): number {
  let n = node.workspaces.length;
  for (const child of node.folders.values()) n += countNodeWorkspaces(child);
  return n;
}
