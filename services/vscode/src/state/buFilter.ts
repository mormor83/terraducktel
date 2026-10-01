/** The subset of `vscode.Memento` the filter needs — small enough to fake without the vscode stub. */
export interface Memento {
  get<T>(key: string): T | undefined;
  update(key: string, value: unknown): Thenable<void> | Promise<void>;
}

/** The filter is stored as the BUs the user HID (not the ones they kept), per profile, so a BU
 *  that appears later is visible by default. */
export const hiddenKey = (profile: string) => `buHidden.${profile}`;

export function getHidden(m: Memento, profile: string): string[] {
  const v = m.get<unknown>(hiddenKey(profile));
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

export const visibleSlugs = (all: readonly string[], hidden: readonly string[]) => all.filter((s) => !hidden.includes(s));

/** Persists "show exactly `visible` out of `all`". An empty selection (after ignoring slugs that
 *  aren't in `all`) is rejected without touching the stored filter. */
export async function setVisible(m: Memento, profile: string, all: readonly string[], visible: readonly string[]): Promise<void> {
  const keep = new Set(visible.filter((s) => all.includes(s)));
  if (!keep.size) throw new Error("Select at least one business unit.");
  const hidden = all.filter((s) => !keep.has(s));
  await m.update(hiddenKey(profile), hidden.length ? hidden : undefined);
}
