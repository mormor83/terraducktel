import type { ActiveTag } from "./tagFilter";
import type { Workspace } from "./types";

/**
 * Does this workspace satisfy the active tag filter?
 *
 * Mirrors `workspace_tags.matches()` on the API side: an exact value match, or
 * presence-only when `value` is null. Kept as a pure function (like paths.ts)
 * so the semantics are testable without mounting the tree — the filter silently
 * hiding a row is the kind of bug you only notice by counting.
 *
 * The two implementations are deliberately duplicated rather than shared: the
 * server filters `GET /workspaces?tag=`, the client filters an already-loaded
 * list, and one of them has to exist in each language regardless.
 */
export function matchesTag(ws: Workspace, active: ActiveTag): boolean {
  if (!active) return true;
  const value = (ws.tags ?? {})[active.key];
  if (value === undefined) return false;
  return active.value === null || value === active.value;
}

/** `{team: "pay"}` → `"team=pay"`, for folding tags into the fuzzy text search. */
export function tagsAsSearchText(
  tags: Record<string, string> | null | undefined,
): string {
  return Object.entries(tags ?? {})
    .map(([k, v]) => `${k}=${v}`)
    .join(" ");
}
