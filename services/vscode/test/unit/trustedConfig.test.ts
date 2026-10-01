import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { CREDENTIAL_ROUTING_KEYS, ignoredWorkspaceOverrides, readUserProfiles, userLevel, type InspectableConfig } from "../../src/auth/trustedConfig";

type Scoped = { defaultValue?: unknown; globalValue?: unknown; workspaceValue?: unknown; workspaceFolderValue?: unknown };
/** Mimics `WorkspaceConfiguration.inspect` over per-scope values. */
const cfg = (values: Record<string, Scoped>): InspectableConfig => ({
  inspect: <T>(key: string) => (values[key] ?? { defaultValue: undefined }) as { defaultValue?: T; globalValue?: T; workspaceValue?: T; workspaceFolderValue?: T },
});

describe("trusted (user-scope-only) profile settings", () => {
  const user = { prod: "https://tdt.example.com" };

  it("ignores a workspace-level URL override of an existing profile", () => {
    const c = cfg({
      profiles: { defaultValue: {}, globalValue: user, workspaceValue: { prod: "https://evil.example.net" } },
    });
    expect(readUserProfiles(c)).toEqual([{ name: "prod", url: "https://tdt.example.com", uiUrl: undefined, insecureTls: false }]);
  });

  it("ignores a folder-level override and profiles that only a workspace defines", () => {
    const c = cfg({
      profiles: { defaultValue: {}, globalValue: user, workspaceFolderValue: { prod: "https://evil.example.net", extra: "https://evil.example.net" } },
    });
    expect(readUserProfiles(c).map((p) => [p.name, p.url])).toEqual([["prod", "https://tdt.example.com"]]);
  });

  it("gives an attacker nothing when the user has no profiles at all", () => {
    expect(readUserProfiles(cfg({ profiles: { defaultValue: {}, workspaceValue: { prod: "https://evil.example.net" } } }))).toEqual([]);
  });

  it("ignores workspace-level insecureTlsProfiles and uiUrls", () => {
    const c = cfg({
      profiles: { defaultValue: {}, globalValue: user },
      uiUrls: { defaultValue: {}, workspaceValue: { prod: "https://evil.example.net" } },
      insecureTlsProfiles: { defaultValue: [], workspaceValue: ["prod"] },
    });
    expect(readUserProfiles(c)).toEqual([{ name: "prod", url: "https://tdt.example.com", uiUrl: undefined, insecureTls: false }]);
  });

  it("still honours user-level uiUrls / insecureTlsProfiles", () => {
    const c = cfg({
      profiles: { defaultValue: {}, globalValue: { dev: "https://localhost:8001" } },
      uiUrls: { defaultValue: {}, globalValue: { dev: "http://localhost:3001" } },
      insecureTlsProfiles: { defaultValue: [], globalValue: ["dev"] },
    });
    expect(readUserProfiles(c)).toEqual([{ name: "dev", url: "https://localhost:8001", uiUrl: "http://localhost:3001", insecureTls: true }]);
  });

  it("userLevel ignores a workspace activeProfile and falls back to the default", () => {
    expect(userLevel(cfg({ activeProfile: { defaultValue: "", workspaceValue: "attacker" } }), "activeProfile")).toBe("");
    expect(userLevel(cfg({ activeProfile: { defaultValue: "", globalValue: "prod", workspaceValue: "attacker" } }), "activeProfile")).toBe("prod");
  });

  it("reports which keys a workspace tried to set", () => {
    expect(ignoredWorkspaceOverrides(cfg({
      profiles: { globalValue: user, workspaceValue: {} },
      insecureTlsProfiles: { workspaceFolderValue: ["prod"] },
      uiUrls: { globalValue: {} },
    }))).toEqual(["profiles", "insecureTlsProfiles"]);
  });

  it("package.json makes every credential-routing setting application-scoped and restricted in untrusted workspaces", () => {
    const pkg = JSON.parse(readFileSync(join(__dirname, "..", "..", "package.json"), "utf8"));
    const props = pkg.contributes.configuration.properties;
    for (const k of CREDENTIAL_ROUTING_KEYS) expect(props[`terraducktel.${k}`].scope, k).toBe("application");
    expect(pkg.capabilities.untrustedWorkspaces.restrictedConfigurations.sort()).toEqual(CREDENTIAL_ROUTING_KEYS.map((k) => `terraducktel.${k}`).sort());
  });
});
