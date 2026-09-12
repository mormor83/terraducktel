import * as vscode from "vscode";
import type { Session } from "../session";
import { readProfiles } from "../auth/profiles";

export function wrap(fn: (...a: unknown[]) => Promise<unknown>) {
  return async (...a: unknown[]) => { try { await fn(...a); } catch (e) { void vscode.window.showErrorMessage(`Terraducktel: ${e instanceof Error ? e.message : String(e)}`); } };
}

export function registerAuthCommands(ctx: vscode.ExtensionContext, s: Session) {
  ctx.subscriptions.push(
    vscode.commands.registerCommand("terraducktel.signIn", wrap(() => s.signIn())),
    vscode.commands.registerCommand("terraducktel.signOut", wrap(async () => { await s.signOut(); void vscode.window.showInformationMessage("Terraducktel: signed out."); })),
    vscode.commands.registerCommand("terraducktel.refresh", wrap(() => s.store.refresh())),
    vscode.commands.registerCommand("terraducktel.switchProfile", wrap(async () => {
      const profiles = readProfiles(vscode.workspace.getConfiguration("terraducktel").get("profiles"));
      if (!profiles.length) { void vscode.commands.executeCommand("workbench.action.openSettings", "terraducktel.profiles"); return; }
      const pick = await vscode.window.showQuickPick(profiles.map((p) => ({ label: p.name, description: p.url })), { placeHolder: "Active Terraducktel profile" });
      if (pick) await vscode.workspace.getConfiguration("terraducktel").update("activeProfile", pick.label, vscode.ConfigurationTarget.Global);
    })),
    vscode.commands.registerCommand("terraducktel.switchBusinessUnit", wrap(async () => {
      const c = s.requireClient();
      if (s.tokens?.kind() === "api_key") throw new Error("API keys are bound to one business unit.");
      const bus = await c.listBusinessUnits();
      const items = bus.map((b) => ({ label: b.slug, description: b.name }));
      if (s.tokens?.claims()?.is_superadmin) items.unshift({ label: "all", description: "every business unit (superadmin)" });
      const pick = await vscode.window.showQuickPick(items, { placeHolder: `Business unit (current: ${s.bu || "default"})` });
      if (pick) await s.setBu(pick.label);
    })),
  );
}
