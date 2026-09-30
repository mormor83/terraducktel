// Selection model for the Compare screen: which hunks / creates per pair, plus
// the reasons given for protected hunks. Pure helpers, no React.

import type { CompareResult, Direction, Hunk, PromotionSelection } from "../../../api/envLinks";

export type PairPick = { hunks: Set<string>; create: boolean };
export type Picks = Map<string, PairPick>;
export type Overrides = Map<string, string>; // hunk_id -> reason

export function countSelection(picks: Picks): { changes: number; stacks: number } {
  let changes = 0;
  let stacks = 0;
  for (const p of picks.values()) {
    const n = p.hunks.size + (p.create ? 1 : 0);
    if (n > 0) {
      changes += n;
      stacks += 1;
    }
  }
  return { changes, stacks };
}

export function toSelection(
  direction: Direction,
  picks: Picks,
  overrides: Overrides,
): PromotionSelection {
  const pairs = [...picks.entries()]
    .filter(([, p]) => p.hunks.size > 0 || p.create)
    .map(([pair_id, p]) => ({ pair_id, hunk_ids: [...p.hunks].sort(), create_in_target: p.create }));
  const selected = new Set(pairs.flatMap((p) => p.hunk_ids));
  return {
    direction,
    confirm_reverse: direction === "reverse",
    pairs,
    protected_overrides: [...overrides.entries()]
      .filter(([id]) => selected.has(id))
      .map(([hunk_id, reason]) => ({ hunk_id, reason })),
  };
}

/** Hunks that travel with `h` (same non-null group), including `h`. */
export function groupOf(h: Hunk, all: Hunk[]): Hunk[] {
  if (!h.group) return [h];
  return all.filter((x) => x.group === h.group);
}

export function selectable(h: Hunk): boolean {
  return h.classification !== "backend" && h.applicable;
}

/** Promotable hunks whose whole group is promotable + applicable. */
export function allPromotable(compare: CompareResult | undefined): string[] {
  const hunks = compare?.config_diff?.hunks ?? [];
  return hunks
    .filter((h) => h.classification === "promotable" && h.applicable)
    .filter((h) => groupOf(h, hunks).every((g) => g.classification === "promotable" && g.applicable))
    .map((h) => h.id);
}
