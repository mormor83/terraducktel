import * as vscode from "vscode";
import type { Session } from "../session";
import type { Profile } from "../auth/profiles";
import { readUserProfiles } from "../auth/trustedConfig";
import { GLOBALSTATE_ACTIVE_PROFILE } from "../ids";

export function wrap(fn: (...a: unknown[]) => Promise<unknown>) {
  return async (...a: unknown[]) => { try { await fn(...a); } catch (e) { void vscode.window.showErrorMessage(`Terraducktel: ${e instanceof Error ? e.message : String(e)}`); } };
}

type StringMap = Record<string, string>;

/** User-scope profiles only (see auth/trustedConfig.ts) — also what `writeProfiles` rewrites, so
 *  Add/Remove profile can never copy a workspace-level value into the user's settings. */
function currentProfiles(cfg: vscode.WorkspaceConfiguration): Profile[] {
  return readUserProfiles(cfg);
}

/** Rewrites all three profile settings from a full `Profile[]`, always in the Settings-UI-native
 *  map form — so adding or removing a profile also migrates any leftover legacy-array entries the
 *  user never converted by hand. */
async function writeProfiles(cfg: vscode.WorkspaceConfiguration, profiles: Profile[]): Promise<void> {
  const profileMap: StringMap = {}; const uiMap: StringMap = {}; const insecure: string[] = [];
  for (const p of profiles) {
    profileMap[p.name] = p.url;
    if (p.uiUrl) uiMap[p.name] = p.uiUrl;
    if (p.insecureTls) insecure.push(p.name);
  }
  await cfg.update("profiles", profileMap, vscode.ConfigurationTarget.Global);
  await cfg.update("uiUrls", uiMap, vscode.ConfigurationTarget.Global);
  await cfg.update("insecureTlsProfiles", insecure, vscode.ConfigurationTarget.Global);
}

const PROFILE_NAME_RE = /^[a-z0-9][a-z0-9._-]{0,39}$/;

export function registerAuthCommands(ctx: vscode.ExtensionContext, s: Session) {
  ctx.subscriptions.push(
    vscode.commands.registerCommand("terraducktel.signIn", wrap(() => s.signIn())),
    vscode.commands.registerCommand("terraducktel.signOut", wrap(async () => { await s.signOut(); void vscode.window.showInformationMessage("Terraducktel: signed out."); })),
    vscode.commands.registerCommand("terraducktel.refresh", wrap(() => s.store.refresh())),
    vscode.commands.registerCommand("terraducktel.switchProfile", wrap(async () => {
      const profiles = currentProfiles(vscode.workspace.getConfiguration("terraducktel"));
      if (!profiles.length) { void vscode.commands.executeCommand("terraducktel.addProfile"); return; }
      const pick = await vscode.window.showQuickPick(
        profiles.map((p) => ({ label: p.name, description: p.url, detail: p.name === s.profile?.name ? "current" : undefined })),
        { placeHolder: "Active Terraducktel profile" },
      );
      if (pick) await s.setActiveProfile(pick.label);
    })),
    vscode.commands.registerCommand("terraducktel.addProfile", wrap(async () => {
      const name = await vscode.window.showInputBox({
        prompt: "Profile name",
        placeHolder: "prod",
        validateInput: (v) => (PROFILE_NAME_RE.test(v.trim()) ? undefined : "Lowercase letters, digits, '.', '_', '-'; must start with a letter or digit; 40 characters max."),
      });
      if (!name) return;
      const profileName = name.trim();
      const api = await vscode.window.showInputBox({
        prompt: `API origin for '${profileName}'`,
        placeHolder: "https://tdt.example.com",
        validateInput: (v) => (/^https?:\/\//.test(v.trim()) ? undefined : "Must start with http:// or https://"),
      });
      if (!api) return;
      const apiUrl = api.trim().replace(/\/+$/, "");
      const uiInput = await vscode.window.showInputBox({
        prompt: "Web UI origin (leave empty if it's the same as the API origin)",
        placeHolder: "http://localhost:3001",
      });
      if (uiInput === undefined) return;
      const uiUrl = uiInput.trim().replace(/\/+$/, "");
      const tlsPick = await vscode.window.showQuickPick(
        [{ label: "No", picked: true, insecure: false }, { label: "Yes", insecure: true }],
        { placeHolder: "Skip TLS certificate verification? (self-signed dev stacks only)" },
      );
      if (!tlsPick) return;

      const cfg = vscode.workspace.getConfiguration("terraducktel");
      const existing = currentProfiles(cfg);
      const profiles = existing.filter((p) => p.name !== profileName);
      profiles.push({ name: profileName, url: apiUrl, uiUrl: uiUrl || undefined, insecureTls: tlsPick.insecure });
      await writeProfiles(cfg, profiles);
      await s.setActiveProfile(profileName);

      const action = await vscode.window.showInformationMessage(`Terraducktel: profile '${profileName}' added and made active.`, "Sign in now");
      if (action) await vscode.commands.executeCommand("terraducktel.signIn");
    })),
    vscode.commands.registerCommand("terraducktel.removeProfile", wrap(async () => {
      const cfg = vscode.workspace.getConfiguration("terraducktel");
      const profiles = currentProfiles(cfg);
      if (!profiles.length) { void vscode.window.showInformationMessage("Terraducktel: no profiles to remove."); return; }
      const pick = await vscode.window.showQuickPick(profiles.map((p) => ({ label: p.name, description: p.url })), { placeHolder: "Remove which profile?" });
      if (!pick) return;
      const confirm = await vscode.window.showWarningMessage(`Remove Terraducktel profile '${pick.label}'? This also deletes its stored credentials.`, { modal: true }, "Remove");
      if (confirm !== "Remove") return;

      await writeProfiles(cfg, profiles.filter((p) => p.name !== pick.label));
      if (s.profile?.name === pick.label) await ctx.globalState.update(GLOBALSTATE_ACTIVE_PROFILE, undefined);
      await ctx.secrets.delete(`terraducktel.cred.${pick.label}`);
      void vscode.window.showInformationMessage(`Terraducktel: removed profile '${pick.label}'.`);
    })),
    vscode.commands.registerCommand("terraducktel.filterBusinessUnits", wrap(async () => {
      const visible = new Set(s.visibleSlugs());
      const items = s.store.bus.map((b) => ({ label: b.name || b.slug, description: b.slug, picked: visible.has(b.slug), slug: b.slug }));
      if (!items.length) { void vscode.window.showInformationMessage("Terraducktel: no business units to filter yet."); return; }
      const picks = await vscode.window.showQuickPick(items, { canPickMany: true, placeHolder: "Business units to show (checked = visible)", title: "Terraducktel: filter business units" });
      if (!picks) return;
      if (!picks.length) { void vscode.window.showErrorMessage("Terraducktel: select at least one business unit."); return; }
      await s.setVisibleBus(picks.map((p) => p.slug));
    })),
  );
}
