import { readProfiles, type Profile } from "./profiles";

/**
 * Settings that decide WHERE a stored credential is sent (API origin, TLS verification) or WHICH
 * credential is used. A repository can ship `.vscode/settings.json` (or a `.code-workspace` file)
 * and VS Code merges those values over the user's own — so if any of these were read through the
 * normal merged `get()`, cloning a malicious repo could point the `prod` profile at an attacker's
 * host and the extension would hand it the prod refresh token / API key on the next poll.
 *
 * package.json declares all of them `"scope": "application"` (VS Code then refuses to apply them
 * from workspace/folder settings) and lists them under `untrustedWorkspaces.restrictedConfigurations`.
 * This module is the second, code-level line of defence: it only ever reads the User-scope
 * (`globalValue`) or built-in default, and ignores workspace / folder / language overrides even if
 * an older VS Code or a future manifest edit would have let one through.
 */
export const CREDENTIAL_ROUTING_KEYS = ["profiles", "uiUrls", "insecureTlsProfiles", "activeProfile"] as const;
export type CredentialRoutingKey = (typeof CREDENTIAL_ROUTING_KEYS)[number];

/** The subset of `vscode.WorkspaceConfiguration` this module needs (testable without the editor). */
export interface InspectableConfig {
  inspect<T>(key: string): {
    defaultValue?: T; globalValue?: T; workspaceValue?: T; workspaceFolderValue?: T;
    defaultLanguageValue?: T; globalLanguageValue?: T; workspaceLanguageValue?: T; workspaceFolderLanguageValue?: T;
  } | undefined;
}

/** User-scope value of `key`, else its default. Workspace/folder values are never consulted. */
export function userLevel<T>(cfg: InspectableConfig, key: CredentialRoutingKey): T | undefined {
  const i = cfg.inspect<T>(key);
  return i?.globalValue ?? i?.defaultValue;
}

/** Profiles as configured in the user's own settings — never from the opened folder/workspace. */
export function readUserProfiles(cfg: InspectableConfig): Profile[] {
  return readProfiles(userLevel(cfg, "profiles"), userLevel(cfg, "uiUrls"), userLevel(cfg, "insecureTlsProfiles"));
}

/** Keys the opened workspace/folder tries to set; they are ignored, but worth a log line so a user
 *  wondering why their `.vscode/settings.json` profile does nothing gets an answer. */
export function ignoredWorkspaceOverrides(cfg: InspectableConfig): CredentialRoutingKey[] {
  return CREDENTIAL_ROUTING_KEYS.filter((k) => {
    const i = cfg.inspect<unknown>(k);
    return !!i && [i.workspaceValue, i.workspaceFolderValue, i.workspaceLanguageValue, i.workspaceFolderLanguageValue].some((v) => v !== undefined);
  });
}
