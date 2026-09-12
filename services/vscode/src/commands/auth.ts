import * as vscode from "vscode";
import type { Session } from "../session";
import { readProfiles, type Profile } from "../auth/profiles";
import { migrateLegacyBu } from "../auth/bu";
import { GLOBALSTATE_ACTIVE_PROFILE } from "../ids";

export function wrap(fn: (...a: unknown[]) => Promise<unknown>) {
  return async (...a: unknown[]) => { try { await fn(...a); } catch (e) { void vscode.window.showErrorMessage(`Terraducktel: ${e instanceof Error ? e.message : String(e)}`); } };
}

type StringMap = Record<string, string>;

function currentProfiles(cfg: vscode.WorkspaceConfiguration): Profile[] {
  return readProfiles(cfg.get("profiles"), cfg.get("uiUrls"), cfg.get("insecureTlsProfiles"));
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
      // Rewriting always goes through the map form, which has no `bu` field — preserve any
      // legacy per-profile default BU (for every profile, not just the one being added) into
      // globalState before it's dropped.
      await migrateLegacyBu(existing, ctx.globalState);
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

      await migrateLegacyBu(profiles, ctx.globalState);
      await writeProfiles(cfg, profiles.filter((p) => p.name !== pick.label));
      if (s.profile?.name === pick.label) await ctx.globalState.update(GLOBALSTATE_ACTIVE_PROFILE, undefined);
      await ctx.secrets.delete(`terraducktel.cred.${pick.label}`);
      void vscode.window.showInformationMessage(`Terraducktel: removed profile '${pick.label}'.`);
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
