import * as vscode from "vscode";
import type { BusinessUnit, Run, RunStep, Workspace } from "../api/types";
import type { CloudGroup, FolderNode, RegionGroup } from "../state/grouping";
import { statusIcon } from "./brand";

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

export function stepDescription(step: RunStep): string { return step.duration_seconds ? `${step.duration_seconds}s` : ""; }

export type Node = BuNode | FilterHeaderNode | CloudNode | RegionNode | FolderTreeNode | WorkspaceNode | RunNode | StepNode | MessageNode;
/** Top-level node of both trees: one per visible business unit. Every descendant carries `bu`
 *  (the slug), so actions can bind a client to the BU the node lives in. */
export class BuNode extends vscode.TreeItem {
  constructor(public readonly buUnit: BusinessUnit, description: string, expanded: boolean) {
    super(buUnit.name || buUnit.slug, expanded ? vscode.TreeItemCollapsibleState.Expanded : vscode.TreeItemCollapsibleState.Collapsed);
    this.id = `bu:${buUnit.slug}`; this.contextValue = "bu"; this.description = description; this.iconPath = new vscode.ThemeIcon("organization", new vscode.ThemeColor("terraducktel.accent"));
    this.tooltip = `${buUnit.name} (${buUnit.slug})`;
  }
  get bu() { return this.buUnit.slug; }
}
/** First row of the Workspaces tree while some BUs are filtered out; click opens the filter picker. */
export class FilterHeaderNode extends vscode.TreeItem {
  constructor(visible: number, total: number) {
    super(`Showing ${visible} of ${total} business units — Filter…`, vscode.TreeItemCollapsibleState.None);
    this.contextValue = "filterHeader"; this.iconPath = new vscode.ThemeIcon("filter");
    this.command = { command: "terraducktel.filterBusinessUnits", title: "Filter business units" };
  }
}
export class CloudNode extends vscode.TreeItem { constructor(public bu: string, public group: CloudGroup) { super(`${group.label}`, vscode.TreeItemCollapsibleState.Collapsed); this.description = `${group.cloud.toUpperCase()} · ${group.count}`; this.contextValue = "cloud"; this.iconPath = new vscode.ThemeIcon(CLOUD_ICON[group.cloud] ?? "cloud", new vscode.ThemeColor("terraducktel.accent")); this.id = `cloud:${bu}:${group.cloud}:${group.key}`; } }
export class RegionNode extends vscode.TreeItem { constructor(public bu: string, public group: CloudGroup, public region: RegionGroup) { super(region.region, vscode.TreeItemCollapsibleState.Expanded); this.description = String(region.count); this.contextValue = "region"; this.iconPath = new vscode.ThemeIcon("location"); this.id = `region:${bu}:${group.cloud}:${group.key}:${region.region}`; } }
export class FolderTreeNode extends vscode.TreeItem { constructor(public bu: string, public folder: FolderNode, public path: string) { super(folder.name, vscode.TreeItemCollapsibleState.Expanded); this.contextValue = "folder"; this.iconPath = vscode.ThemeIcon.Folder; this.id = `folder:${bu}:${path}`; } }
export class WorkspaceNode extends vscode.TreeItem {
  constructor(public bu: string, public ws: Workspace, leaf: string, last: Run | undefined, runCount: number) {
    super(leaf, runCount ? vscode.TreeItemCollapsibleState.Collapsed : vscode.TreeItemCollapsibleState.None);
    this.id = `ws:${ws.id}`; this.contextValue = "workspace"; this.description = workspaceDescription(ws, last);
    this.iconPath = statusIcon(last?.status);
    const md = new vscode.MarkdownString(); md.appendMarkdown(`**${ws.name}**  \n\`${ws.tf_working_dir}\`  \nid \`${ws.id}\`  \nenv ${ws.environment} · kind ${ws.kind} · branch ${ws.repo_ref} · drift ${ws.drift_status} · path ${ws.path_status}`);
    if (ws.repo_url) md.appendMarkdown(`  \nrepo ${ws.repo_url}`);
    const tags = Object.entries(ws.tags ?? {}); if (tags.length) md.appendMarkdown(`  \ntags ${tags.map(([k, v]) => `${k}=${v}`).join(", ")}`);
    this.tooltip = md;
  }
}
export class RunNode extends vscode.TreeItem {
  readonly bu: string;
  /** `collapsible` is opt-in: in the Runs view a run expands to its steps, but as a child of a
   *  workspace in the Workspaces view it is a leaf (that tree does not fetch steps). */
  constructor(public run: Run, opts: { bu: string; showWorkspace?: string; collapsible?: boolean }) {
    super(opts.showWorkspace ? `${opts.showWorkspace} · ${run.command}` : run.command,
      opts.collapsible ? vscode.TreeItemCollapsibleState.Collapsed : vscode.TreeItemCollapsibleState.None);
    this.bu = opts.bu; this.id = `run:${run.id}`; this.contextValue = runContextValue(run); this.description = describeRun(run).replace(/^[^·]+· /, "");
    this.iconPath = statusIcon(run.status); this.tooltip = `${run.command} ${run.status}\n${run.id}\nbranch ${run.branch ?? "-"}\ncreated ${run.created_at ?? "-"}`;
    this.command = { command: "terraducktel.watchRun", title: "Watch run", arguments: [this] };
  }
}
export class StepNode extends vscode.TreeItem {
  constructor(public run: Run, public step: RunStep) {
    super(step.name, vscode.TreeItemCollapsibleState.None);
    this.id = `step:${run.id}:${step.position}`; this.contextValue = "step";
    this.description = stepDescription(step);
    this.iconPath = statusIcon(step.status);
    this.tooltip = `${step.name} · ${step.status}`;
  }
}
export class MessageNode extends vscode.TreeItem { constructor(msg: string, icon = "info") { super(msg, vscode.TreeItemCollapsibleState.None); this.contextValue = "message"; this.iconPath = new vscode.ThemeIcon(icon); } }
