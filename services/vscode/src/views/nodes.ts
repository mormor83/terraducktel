import * as vscode from "vscode";
import type { Run, Workspace } from "../api/types";
import type { CloudGroup, FolderNode, RegionGroup } from "../state/grouping";

export function statusIconId(status: string): string {
  switch (status) {
    case "applied": return "pass";
    case "planned": return "check";
    case "awaiting_approval": return "bell";
    case "failed": return "error";
    case "cancelled": return "circle-slash";
    case "pending": return "clock";
    case "running": case "planning": case "applying": return "sync~spin";
    default: return "circle-outline";
  }
}
export function statusIcon(status: string): vscode.ThemeIcon {
  const color = status === "failed" ? "testing.iconFailed" : status === "applied" || status === "planned" ? "testing.iconPassed" : status === "awaiting_approval" ? "notificationsWarningIcon.foreground" : undefined;
  return new vscode.ThemeIcon(statusIconId(status), color ? new vscode.ThemeColor(color) : undefined);
}
export function runContextValue(run: Run): string { return `run.${run.status}`; }
export function describeRun(run: Run): string {
  const when = run.created_at ? new Date(run.created_at).toLocaleString() : "";
  return [run.command, run.status, run.branch ?? "", run.id.slice(0, 8), when].filter(Boolean).join(" · ");
}
export function workspaceDescription(ws: Workspace, last: Run | undefined): string {
  const bits = [last ? last.status : "no runs", ws.repo_ref];
  if (ws.drift_status === "drifted") bits.push("drift");
  return bits.join(" · ");
}
const CLOUD_ICON: Record<string, string> = { aws: "cloud", azure: "azure", gcp: "globe", other: "server-environment" };

export type Node = CloudNode | RegionNode | FolderTreeNode | WorkspaceNode | RunNode | MessageNode;
export class CloudNode extends vscode.TreeItem { constructor(public group: CloudGroup) { super(`${group.label}`, vscode.TreeItemCollapsibleState.Collapsed); this.description = `${group.cloud.toUpperCase()} · ${group.count}`; this.contextValue = "cloud"; this.iconPath = new vscode.ThemeIcon(CLOUD_ICON[group.cloud] ?? "cloud"); this.id = `cloud:${group.cloud}:${group.key}`; } }
export class RegionNode extends vscode.TreeItem { constructor(public group: CloudGroup, public region: RegionGroup) { super(region.region, vscode.TreeItemCollapsibleState.Expanded); this.description = String(region.count); this.contextValue = "region"; this.iconPath = new vscode.ThemeIcon("location"); this.id = `region:${group.cloud}:${group.key}:${region.region}`; } }
export class FolderTreeNode extends vscode.TreeItem { constructor(public folder: FolderNode, public path: string) { super(folder.name, vscode.TreeItemCollapsibleState.Expanded); this.contextValue = "folder"; this.iconPath = vscode.ThemeIcon.Folder; this.id = `folder:${path}`; } }
export class WorkspaceNode extends vscode.TreeItem {
  constructor(public ws: Workspace, leaf: string, last: Run | undefined, runCount: number) {
    super(leaf, runCount ? vscode.TreeItemCollapsibleState.Collapsed : vscode.TreeItemCollapsibleState.None);
    this.id = `ws:${ws.id}`; this.contextValue = "workspace"; this.description = workspaceDescription(ws, last);
    this.iconPath = last ? statusIcon(last.status) : new vscode.ThemeIcon("circle-outline");
    const md = new vscode.MarkdownString(); md.appendMarkdown(`**${ws.name}**  \n\`${ws.tf_working_dir}\`  \nid \`${ws.id}\`  \nenv ${ws.environment} · kind ${ws.kind} · branch ${ws.repo_ref} · drift ${ws.drift_status} · path ${ws.path_status}`);
    if (ws.repo_url) md.appendMarkdown(`  \nrepo ${ws.repo_url}`);
    const tags = Object.entries(ws.tags ?? {}); if (tags.length) md.appendMarkdown(`  \ntags ${tags.map(([k, v]) => `${k}=${v}`).join(", ")}`);
    this.tooltip = md;
  }
}
export class RunNode extends vscode.TreeItem {
  constructor(public run: Run, opts: { showWorkspace?: string } = {}) {
    super(opts.showWorkspace ? `${opts.showWorkspace} · ${run.command}` : run.command, vscode.TreeItemCollapsibleState.None);
    this.id = `run:${run.id}`; this.contextValue = runContextValue(run); this.description = describeRun(run).replace(/^[^·]+· /, "");
    this.iconPath = statusIcon(run.status); this.tooltip = `${run.command} ${run.status}\n${run.id}\nbranch ${run.branch ?? "-"}\ncreated ${run.created_at ?? "-"}`;
    this.command = { command: "terraducktel.watchRun", title: "Watch run", arguments: [this] };
  }
}
export class MessageNode extends vscode.TreeItem { constructor(msg: string, icon = "info") { super(msg, vscode.TreeItemCollapsibleState.None); this.contextValue = "message"; this.iconPath = new vscode.ThemeIcon(icon); } }
