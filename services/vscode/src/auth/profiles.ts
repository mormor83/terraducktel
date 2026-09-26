export interface Profile { name: string; url: string; uiUrl?: string; bu?: string; insecureTls?: boolean }

const normUrl = (u: string) => u.replace(/\/+$/, "");
const isRecord = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object";

/** Legacy `terraducktel.profiles` shape: `[{name,url,uiUrl?,bu?,insecureTls?}]`. */
function fromLegacyRows(rows: unknown[]): Profile[] {
  return rows.flatMap((p) => {
    if (!p || typeof p !== "object") return [];
    const { name, url, uiUrl, bu, insecureTls } = p as Record<string, unknown>;
    if (typeof name !== "string" || !name.trim() || typeof url !== "string" || !/^https?:\/\//.test(url)) return [];
    return [{ name: name.trim(), url: normUrl(url), uiUrl: typeof uiUrl === "string" && uiUrl ? normUrl(uiUrl) : undefined, bu: typeof bu === "string" && bu ? bu : undefined, insecureTls: insecureTls === true }];
  });
}

/**
 * Read + normalise `terraducktel.profiles`; malformed entries are dropped (not thrown).
 *
 * Accepts both the legacy array form (`[{name,url,uiUrl?,bu?,insecureTls?}]`, from before 0.3.1)
 * and the Settings-UI-native map form (`{name: url}`, `additionalProperties: {type:"string"}` so
 * it renders as editable rows in the Settings UI). Map entries are merged with `uiUrls[name]` and
 * `insecureList.includes(name)`.
 *
 * VS Code merges object-typed settings across scopes (default <- user <- workspace <- folder) by
 * shallow-spreading them — and a JS array is `typeof "object"`, so a user who has not migrated an
 * old array-shaped User-scope value and then adds a new map-shaped entry at Workspace scope can
 * end up with BOTH forms in the same raw value: numeric-indexed keys carrying the old row objects
 * alongside named keys carrying new url strings. Handle that by fishing legacy rows out of any
 * numeric-keyed own properties in addition to a plain array `raw`. When a name exists in both,
 * the map entry wins (logged nowhere here — deliberately quiet; a caller wanting to trace the
 * override can compare pre/post `readProfiles` output, since both entries' data is derivable).
 */
export function readProfiles(raw: unknown, uiUrls?: unknown, insecureList?: unknown): Profile[] {
  const byName = new Map<string, Profile>();

  const legacyRows: unknown[] = Array.isArray(raw)
    ? raw
    : isRecord(raw)
      ? Object.entries(raw).filter(([k]) => /^\d+$/.test(k)).map(([, v]) => v)
      : [];
  for (const p of fromLegacyRows(legacyRows)) byName.set(p.name, p);

  if (isRecord(raw) && !Array.isArray(raw)) {
    const uiMap = isRecord(uiUrls) ? uiUrls : {};
    const insecureSet = new Set(Array.isArray(insecureList) ? insecureList.filter((x): x is string => typeof x === "string") : []);
    for (const [name, url] of Object.entries(raw)) {
      if (typeof url !== "string") continue; // numeric-keyed legacy rows already handled above
      const trimmed = name.trim();
      if (!trimmed || !/^https?:\/\//.test(url)) continue;
      const ui = uiMap[name];
      // Map entries always win over a same-named legacy row: `.set()` on an already-populated
      // key just replaces it.
      byName.set(trimmed, { name: trimmed, url: normUrl(url), uiUrl: typeof ui === "string" && ui ? normUrl(ui) : undefined, insecureTls: insecureSet.has(name) });
    }
  }

  return [...byName.values()].sort((a, b) => a.name.localeCompare(b.name));
}

export function uiUrlFor(p: Profile): string { return p.uiUrl ?? p.url.replace(/\/api$/, ""); }
export function pickActive(profiles: Profile[], activeName: string | undefined): Profile | undefined {
  return profiles.find((p) => p.name === activeName) ?? profiles[0];
}
