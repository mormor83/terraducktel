import type { Profile } from "./profiles";

/** The subset of `vscode.Memento` this module needs — small enough to fake in a unit test
 *  without pulling in the `vscode` stub. */
export interface BuStore {
  get<T>(key: string): T | undefined;
  update(key: string, value: unknown): Thenable<void> | Promise<void>;
}

/** Copies each legacy profile's inline `bu` (default business-unit slug — a field the new
 *  Settings-UI-native map schema has no room for) into `globalState["bu.<name>"]`, the same key
 *  `Session.setBu()` already writes to when the in-app "Switch business unit" picker is used.
 *  Only fills in a value that ISN'T already set there, so it never clobbers an explicit in-app
 *  choice with a stale settings default. Call this before any rewrite of the profile settings
 *  that would otherwise drop `bu` silently (`addProfile` / `removeProfile`). */
export async function migrateLegacyBu(profiles: Profile[], store: BuStore): Promise<void> {
  for (const p of profiles) {
    if (!p.bu) continue;
    const key = `bu.${p.name}`;
    if (store.get(key) !== undefined) continue;
    await store.update(key, p.bu);
  }
}
