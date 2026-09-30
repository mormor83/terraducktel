import { PAIR_STATUS_LABEL, PAIR_STATUS_TONE, type EnvPair } from "../../../api/envLinks";
import { Badge, cx } from "../../ui";
import type { Picks } from "./selection";

type Summary = {
  keys_changed?: number;
  protected_count?: number;
  promotable_count?: number;
  module_versions?: { key: string; from: string | null; to: string | null }[];
};

/** "3 keys · module v1.4.0 → v1.6.2 · 2 protected" (target goes from → to). */
export function miniSummary(p: EnvPair): string | null {
  const s = (p.summary ?? null) as Summary | null;
  if (!s) return null;
  const parts: string[] = [];
  if (s.keys_changed) parts.push(`${s.keys_changed} key${s.keys_changed === 1 ? "" : "s"}`);
  const mv = s.module_versions?.[0];
  if (mv && (mv.from || mv.to)) parts.push(`module ${mv.from ?? "—"} → ${mv.to ?? "—"}`);
  if (s.protected_count) parts.push(`${s.protected_count} protected`);
  return parts.length ? parts.join(" · ") : null;
}

export function PairList({
  pairs,
  selectedId,
  picks,
  loadingIds,
  canSelect,
  onSelect,
  onToggle,
}: {
  pairs: EnvPair[];
  selectedId: string | null;
  picks: Picks;
  loadingIds: Set<string>;
  canSelect: (p: EnvPair) => boolean;
  onSelect: (p: EnvPair) => void;
  onToggle: (p: EnvPair) => void;
}) {
  if (pairs.length === 0) {
    return <p className="px-4 py-8 text-center text-[13px] text-brand-muted">No pairs match.</p>;
  }
  return (
    <ul className="divide-y divide-brand-border" aria-label="Pairs">
      {pairs.map((p) => {
        const key = p.id ?? p.relative_path;
        const pick = p.id ? picks.get(p.id) : undefined;
        const checked = !!pick && (pick.hunks.size > 0 || pick.create);
        const summary = miniSummary(p);
        const label = p.relative_path || p.target_stack_name || p.source_stack_name || ".";
        return (
          <li
            key={key}
            className={cx(
              "flex items-start gap-2.5 px-3 py-2.5",
              selectedId === p.id ? "bg-[rgba(var(--td-glow-rgb),0.1)]" : "hover:bg-[rgba(var(--td-edge-rgb),0.05)]",
            )}
          >
            <input
              type="checkbox"
              className="mt-1 accent-[var(--td-rail)]"
              aria-label={`Select ${label}`}
              checked={checked}
              disabled={!canSelect(p)}
              onChange={() => onToggle(p)}
            />
            <button type="button" onClick={() => onSelect(p)} className="min-w-0 flex-1 text-left">
              <span className="block truncate font-mono text-[12.5px] text-brand-text" title={p.relative_path}>
                {label}
              </span>
              <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1">
                <Badge tone={PAIR_STATUS_TONE[p.status]}>{PAIR_STATUS_LABEL[p.status]}</Badge>
                {loadingIds.has(p.id ?? "") && (
                  <span className="font-mono text-[10.5px] text-brand-muted">comparing…</span>
                )}
                {summary && <span className="truncate font-mono text-[11px] text-brand-muted">{summary}</span>}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
