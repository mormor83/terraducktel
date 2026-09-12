export interface Profile { name: string; url: string; uiUrl?: string; bu?: string; insecureTls?: boolean }

/** Read + normalise `terraducktel.profiles`; malformed entries are dropped (not thrown). */
export function readProfiles(raw: unknown): Profile[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((p) => {
    if (!p || typeof p !== "object") return [];
    const { name, url, uiUrl, bu, insecureTls } = p as Record<string, unknown>;
    if (typeof name !== "string" || !name.trim() || typeof url !== "string" || !/^https?:\/\//.test(url)) return [];
    return [{ name: name.trim(), url: url.replace(/\/+$/, ""), uiUrl: typeof uiUrl === "string" && uiUrl ? uiUrl.replace(/\/+$/, "") : undefined, bu: typeof bu === "string" && bu ? bu : undefined, insecureTls: insecureTls === true }];
  });
}
export function uiUrlFor(p: Profile): string { return p.uiUrl ?? p.url.replace(/\/api$/, ""); }
export function pickActive(profiles: Profile[], activeName: string | undefined): Profile | undefined {
  return profiles.find((p) => p.name === activeName) ?? profiles[0];
}
