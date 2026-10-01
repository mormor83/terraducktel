import * as vscode from "vscode";

/** Run / step statuses that are still moving. SVG animation doesn't play in tree icons, so these
 *  get a spinning codicon instead of a brand SVG. */
const IN_FLIGHT = new Set(["running", "planning", "applying"]);

const ASSET: Record<string, string> = {
  applied: "applied", success: "applied",
  planned: "planned",
  awaiting_approval: "awaiting",
  failed: "failed",
  cancelled: "cancelled", skipped: "cancelled",
  pending: "pending",
};

/** Basename under `media/status/` for a settled status (`none` covers "no runs" and unknown
 *  statuses); `undefined` for an in-flight one. */
export function statusAsset(status: string | undefined): string | undefined {
  if (status && IN_FLIGHT.has(status)) return undefined;
  return (status && ASSET[status]) || "none";
}

let mediaRoot: vscode.Uri | undefined;
/** Called once from `activate` — tree items need absolute URIs for their SVG icons. */
export function initBrandIcons(extensionUri: vscode.Uri): void { mediaRoot = vscode.Uri.joinPath(extensionUri, "media", "status"); }

export type StatusIconPath = vscode.ThemeIcon | { light: vscode.Uri; dark: vscode.Uri };

export function statusIcon(status: string | undefined): StatusIconPath {
  const asset = statusAsset(status);
  if (!asset) return new vscode.ThemeIcon("sync~spin", new vscode.ThemeColor("terraducktel.run"));
  // Only reachable before activate() ran initBrandIcons — degrade to a codicon rather than throw
  // out of a tree render.
  if (!mediaRoot) return new vscode.ThemeIcon("circle-outline");
  return { light: vscode.Uri.joinPath(mediaRoot, `${asset}-light.svg`), dark: vscode.Uri.joinPath(mediaRoot, `${asset}-dark.svg`) };
}
