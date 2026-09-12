// Port of services/ui/src/components/workspace-tree/paths.ts (grouping rules only).
// Keep the two in step: same fixtures live in grouping.test.ts and paths.test.ts.
import type { Workspace } from "../api/types";

export type Cloud = "aws" | "azure" | "gcp" | "other";
export interface Classification { cloud: Cloud; key: string; label: string; region: string }

const parts = (ws: Workspace) => (ws.tf_working_dir ?? "").trim().split("/").filter(Boolean);

export function azureInfo(ws: Workspace): { guid: string; region: string } | null {
  const p = parts(ws); if (p[0]?.toLowerCase() !== "azure") return null;
  const m = (p[1] ?? "").match(/^subscription-(.+)$/); if (!m) return null;
  return { guid: m[1], region: p[2] ?? ws.region };
}
export function gcpInfo(ws: Workspace): { projectId: string; region: string } | null {
  const p = parts(ws); if (p[0]?.toLowerCase() !== "gcp") return null;
  const m = (p[1] ?? "").match(/^project-(.+)$/); if (!m) return null;
  return { projectId: m[1], region: p[2] ?? ws.region };
}

/** Which top-level group a workspace belongs to. Explicit links win over path detection;
 *  AWS is the default only when an account id is set; everything else groups by its top folder. */
export function classify(ws: Workspace): Classification {
  if (ws.azure_subscription_id) { const i = azureInfo(ws); return { cloud: "azure", key: ws.azure_subscription_id, label: i ? `subscription-${i.guid}` : "Azure subscription", region: i?.region ?? ws.region }; }
  if (ws.gcp_project_id) { const i = gcpInfo(ws); return { cloud: "gcp", key: ws.gcp_project_id, label: i ? `project-${i.projectId}` : "GCP project", region: i?.region ?? ws.region }; }
  const a = azureInfo(ws); if (a) return { cloud: "azure", key: `guid:${a.guid}`, label: `subscription-${a.guid}`, region: a.region };
  const g = gcpInfo(ws); if (g) return { cloud: "gcp", key: `pid:${g.projectId}`, label: `project-${g.projectId}`, region: g.region };
  if (ws.aws_account_id && ws.aws_account_id !== "global") return { cloud: "aws", key: ws.aws_account_id, label: ws.aws_account_id, region: ws.region };
  const top = parts(ws)[0] ?? "other";
  return { cloud: "other", key: `other:${top}`, label: top, region: ws.region || "global" };
}

export function workspacePathSegments(ws: Workspace): { folders: string[]; leaf: string } {
  const raw = (ws.tf_working_dir ?? "").trim();
  if (!raw || raw === ".") return { folders: [], leaf: ws.name };
  const p = raw.split("/").filter(Boolean);
  if (p[0]?.startsWith("account-")) p.shift();
  let regionToStrip = ws.region;
  const head = p[0]?.toLowerCase();
  if ((head === "azure" && /^subscription-/.test(p[1] ?? "")) || (head === "gcp" && /^project-/.test(p[1] ?? ""))) { p.shift(); p.shift(); regionToStrip = p[0] ?? ws.region; }
  if (p[0] === regionToStrip) p.shift();
  if (p.length === 0) return { folders: [], leaf: ws.name };
  const leaf = p.pop() as string;
  return { folders: p, leaf };
}

export interface FolderNode { name: string; folders: Map<string, FolderNode>; workspaces: Array<{ ws: Workspace; leaf: string }> }
export interface RegionGroup { region: string; root: FolderNode; count: number }
export interface CloudGroup { cloud: Cloud; key: string; label: string; regions: RegionGroup[]; count: number }

function buildFolderTree(workspaces: Workspace[]): FolderNode {
  const root: FolderNode = { name: "", folders: new Map(), workspaces: [] };
  const items = workspaces.map((ws) => ({ ws, ...workspacePathSegments(ws) }));
  for (const { folders } of items) { let cur = root; for (const seg of folders) { let n = cur.folders.get(seg); if (!n) { n = { name: seg, folders: new Map(), workspaces: [] }; cur.folders.set(seg, n); } cur = n; } }
  for (const { ws, folders, leaf } of items) {
    let cur = root; for (const seg of folders) cur = cur.folders.get(seg)!;
    const colliding = cur.folders.get(leaf);
    (colliding ?? cur).workspaces.push({ ws, leaf });
  }
  const sortNode = (n: FolderNode) => { n.workspaces.sort((a, b) => a.leaf.localeCompare(b.leaf)); n.folders = new Map([...n.folders.entries()].sort(([a], [b]) => a.localeCompare(b))); for (const c of n.folders.values()) sortNode(c); };
  sortNode(root);
  return root;
}
export function countNode(n: FolderNode): number { let c = n.workspaces.length; for (const f of n.folders.values()) c += countNode(f); return c; }

const CLOUD_ORDER: Cloud[] = ["aws", "azure", "gcp", "other"];
export function buildTree(workspaces: Workspace[]): CloudGroup[] {
  const groups = new Map<string, { cls: Classification; byRegion: Map<string, Workspace[]> }>();
  for (const ws of workspaces) {
    const cls = classify(ws); const gk = `${cls.cloud}|${cls.key}`;
    const g = groups.get(gk) ?? { cls, byRegion: new Map() }; groups.set(gk, g);
    const list = g.byRegion.get(cls.region) ?? []; list.push(ws); g.byRegion.set(cls.region, list);
  }
  return [...groups.values()]
    .map(({ cls, byRegion }) => {
      const regions = [...byRegion.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([region, list]) => { const root = buildFolderTree(list); return { region, root, count: countNode(root) }; });
      return { cloud: cls.cloud, key: cls.key, label: cls.label, regions, count: regions.reduce((s, r) => s + r.count, 0) };
    })
    .sort((a, b) => CLOUD_ORDER.indexOf(a.cloud) - CLOUD_ORDER.indexOf(b.cloud) || a.label.localeCompare(b.label));
}
