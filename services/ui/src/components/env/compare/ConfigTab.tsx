import { useEffect, useRef, useState } from "react";

import type { CompareResult, Direction, EnvPair, Hunk, HunkCategory } from "../../../api/envLinks";
import { Badge, Button, EmptyState, Skeleton, cx } from "../../ui";
import { RawDiff, UnifiedDiff } from "./RawDiff";
import { allPromotable, groupOf, selectable, type Overrides, type PairPick } from "./selection";

const CATEGORY_LABEL: Record<HunkCategory, string> = {
  module: "Module & versions",
  inputs: "Inputs",
  providers: "Providers & lock",
  other: "Other",
};
const CATEGORY_ORDER: HunkCategory[] = ["module", "inputs", "providers", "other"];

function Lock() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden>
      <rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" />
    </svg>
  );
}

function ClassBadge({ h }: { h: Hunk }) {
  if (h.classification === "backend") return <Badge tone="neutral">never promoted</Badge>;
  if (h.classification === "protected") {
    return (
      <span title={h.protected_reason ?? "Protected value"}>
        <Badge tone="warning" className="gap-1">
          <Lock /> protected
        </Badge>
      </span>
    );
  }
  return <Badge tone="success">promotable</Badge>;
}

function Value({ v }: { v: string | null }) {
  if (v === null) return <span className="text-brand-muted">—</span>;
  return (
    <span className="block max-h-24 overflow-auto whitespace-pre-wrap break-all font-mono text-[11.5px] text-brand-text">
      {v}
    </span>
  );
}

function ReasonDialog({
  keys,
  initial,
  onCancel,
  onConfirm,
}: {
  keys: string[];
  initial: string;
  onCancel: () => void;
  onConfirm: (reason: string) => void;
}) {
  const [reason, setReason] = useState(initial);
  const ref = useRef<HTMLTextAreaElement | null>(null);
  useEffect(() => {
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4" role="dialog" aria-modal="true" aria-label="Include protected value">
      <div className="w-full max-w-md rounded-[14px] border border-brand-border bg-brand-surface p-5 shadow-xl">
        <h3 className="font-display text-[16px] font-semibold text-brand-text">Include a protected value?</h3>
        <p className="mt-1.5 text-[12.5px] text-brand-muted">
          These keys are environment-specific and normally stay out of a promotion. Say why this one should go across — the
          reason is recorded in the audit log.
        </p>
        <ul className="mt-2 space-y-0.5">
          {keys.map((k) => (
            <li key={k} className="truncate font-mono text-[11.5px] text-[var(--td-warn-ink)]">{k}</li>
          ))}
        </ul>
        <label htmlFor="protected-reason" className="mb-1 mt-3 block font-mono text-[10px] uppercase tracking-[1.3px] text-brand-muted">
          Reason
        </label>
        <textarea
          id="protected-reason"
          ref={ref}
          rows={3}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          className="w-full rounded-md border border-brand-border bg-brand-surface p-2 text-[12.5px] text-brand-text"
        />
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button variant="warning" disabled={!reason.trim()} onClick={() => onConfirm(reason.trim())}>
            Include
          </Button>
        </div>
      </div>
    </div>
  );
}

export function ConfigTab({
  pair,
  compare,
  direction,
  pick,
  overrides,
  canEdit,
  onSetHunks,
  onSetCreate,
}: {
  pair: EnvPair;
  compare: CompareResult | undefined;
  direction: Direction;
  pick: PairPick | undefined;
  overrides: Overrides;
  canEdit: boolean;
  /** Replace the pair's selected hunk set; `reasons` adds protected overrides. */
  onSetHunks: (ids: Set<string>, reasons?: Map<string, string>) => void;
  onSetCreate: (on: boolean) => void;
}) {
  const [view, setView] = useState<"semantic" | "raw">("semantic");
  const [asking, setAsking] = useState<Hunk[] | null>(null);

  const reversedMissing =
    (direction === "forward" && pair.status === "missing_in_source") ||
    (direction === "reverse" && pair.status === "missing_in_target");
  if (pair.status === "excluded") {
    return (
      <EmptyState
        title="Excluded from this link"
        description={pair.reason ? `Reason: ${pair.reason}. Edit the link's overrides to include it.` : "Edit the link's overrides to include it."}
      />
    );
  }
  if (reversedMissing) {
    return (
      <EmptyState
        title="Only exists on the other side"
        description="This stack only exists in the promotion target; there is nothing to promote in this direction."
      />
    );
  }
  if (!compare || (!compare.refs && compare.status === "computing")) {
    return (
      <div className="space-y-2" aria-busy="true">
        <p className="font-mono text-[11px] text-brand-muted">Comparing…</p>
        <Skeleton className="h-10" />
        <Skeleton className="h-40" />
      </div>
    );
  }
  if (compare.error) {
    return (
      <div className="rounded-md border border-[rgba(var(--td-err-rgb),0.3)] bg-[rgba(var(--td-err-rgb),0.08)] p-3 text-[13px] text-[var(--td-err-ink)]">
        {compare.error}
      </div>
    );
  }

  const create = compare.refs?.create_preview;
  if (pair.status === "missing_in_target" && create) {
    return (
      <div>
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <label className={cx("flex items-center gap-2 text-[13px] text-brand-text", !canEdit && "opacity-60")}>
            <input
              type="checkbox"
              className="accent-[var(--td-rail)]"
              checked={!!pick?.create}
              disabled={!canEdit}
              onChange={(e) => onSetCreate(e.target.checked)}
              aria-label="Create in target"
            />
            Create in target
          </label>
          <span className="truncate font-mono text-[12px] text-brand-textSoft">{create.path}</span>
          {create.branch && <span className="font-mono text-[11px] text-brand-muted">⎇ {create.branch}</span>}
        </div>
        {create.substitutions.length > 0 && (
          <p className="mb-2 font-mono text-[11.5px] text-brand-muted">
            substitutions: {create.substitutions.map((s) => `${s.from} → ${s.to}`).join(" · ")}
          </p>
        )}
        {create.warnings.map((w) => (
          <p key={w} className="mb-1 text-[12.5px] text-[var(--td-warn-ink)]">⚠ {w}</p>
        ))}
        <ul className="mt-2 space-y-2">
          {create.files.map((f) => (
            <li key={f.path} className="rounded-md border border-brand-border">
              <div className="flex flex-wrap items-center gap-2 border-b border-brand-border px-3 py-2">
                <span className="font-mono text-[12px] text-brand-text">{f.path}</span>
                {f.changes.map((c) => (
                  <Badge key={c} tone="violet">{c}</Badge>
                ))}
              </div>
              {f.text === null ? (
                <p className="p-3 text-[12px] italic text-brand-muted">binary or oversized — copied as-is</p>
              ) : (
                <pre className="max-h-[320px] overflow-auto p-3 font-mono text-[11.5px] text-brand-textSoft">{f.text}</pre>
              )}
            </li>
          ))}
        </ul>
      </div>
    );
  }

  const diff = compare.config_diff;
  if (!diff) {
    return <EmptyState title="No config diff yet" description="The compare hasn't produced a config diff for this pair." />;
  }
  const hunks = diff.hunks;
  const sel = pick?.hunks ?? new Set<string>();
  const s = diff.summary ?? {};
  const branchesDiffer =
    compare.refs?.source.branch && compare.refs?.target.branch && compare.refs.source.branch !== compare.refs.target.branch;

  const toggle = (h: Hunk) => {
    const group = groupOf(h, hunks);
    const on = group.every((g) => sel.has(g.id));
    const next = new Set(sel);
    if (on) {
      group.forEach((g) => next.delete(g.id));
      onSetHunks(next);
      return;
    }
    const needReason = group.filter((g) => g.classification === "protected" && !overrides.get(g.id));
    if (needReason.length) {
      setAsking(group);
      return;
    }
    group.forEach((g) => next.add(g.id));
    onSetHunks(next);
  };

  const promotableIds = allPromotable(compare);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[11.5px] text-brand-textSoft">
        <span>{s.files_changed ?? 0} files</span>
        <span>{s.keys_changed ?? 0} keys</span>
        <span className="text-[var(--td-green-ink)]">{s.promotable_count ?? 0} promotable</span>
        <span className="text-[var(--td-warn-ink)]">{s.protected_count ?? 0} protected</span>
        {(s.backend_count ?? 0) > 0 && <span>{s.backend_count} backend</span>}
        {(s.module_versions ?? []).map((m) => (
          <span key={m.key}>{m.key}: {m.from ?? "—"} → {m.to ?? "—"}</span>
        ))}
        {(s.providers ?? []).map((m) => (
          <span key={m.key}>{m.key}: {m.from ?? "—"} → {m.to ?? "—"}</span>
        ))}
      </div>
      {branchesDiffer && (
        <p className="mb-2 text-[12.5px] text-[var(--td-warn-ink)]">
          ⚠ The two sides are on different branches ({compare.refs!.source.branch} vs {compare.refs!.target.branch}).
        </p>
      )}
      {diff.warnings.map((w) => (
        <p key={w} className="mb-1 text-[12.5px] text-[var(--td-warn-ink)]">⚠ {w}</p>
      ))}

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="inline-flex rounded-md border border-brand-border p-0.5" role="tablist" aria-label="Diff view">
          {(["semantic", "raw"] as const).map((v) => (
            <button
              key={v}
              type="button"
              role="tab"
              aria-selected={view === v}
              onClick={() => setView(v)}
              className={cx(
                "rounded px-3 py-1 text-[12px] font-medium transition-colors",
                view === v ? "bg-[rgba(var(--td-glow-rgb),0.18)] text-brand-text" : "text-brand-muted hover:text-brand-text",
              )}
            >
              {v === "semantic" ? "Semantic" : "Raw files"}
            </button>
          ))}
        </div>
        {view === "semantic" && canEdit && (
          <>
            <Button variant="secondary" size="sm" disabled={!promotableIds.length}
              onClick={() => onSetHunks(new Set([...sel, ...promotableIds]))}>
              Select all promotable
            </Button>
            <Button variant="ghost" size="sm" disabled={!sel.size} onClick={() => onSetHunks(new Set())}>
              Clear
            </Button>
          </>
        )}
      </div>

      {view === "raw" ? (
        <RawDiff files={diff.files} />
      ) : hunks.length === 0 ? (
        <EmptyState title="In sync" description="No semantic differences between the two sides' config." />
      ) : (
        <div className="space-y-4">
          {CATEGORY_ORDER.filter((c) => hunks.some((h) => h.category === c)).map((cat) => (
            <section key={cat}>
              <h4 className="mb-1.5 font-mono text-[10.5px] uppercase tracking-[1.3px] text-brand-muted">
                {CATEGORY_LABEL[cat]}
              </h4>
              <div className="overflow-x-auto rounded-md border border-brand-border">
                <table className="w-full text-left text-[12.5px]">
                  <thead className="bg-brand-surface2 font-mono text-[10px] uppercase tracking-[1px] text-brand-muted">
                    <tr>
                      <th className="w-8 px-2 py-1.5"><span className="sr-only">Select</span></th>
                      <th className="px-2 py-1.5">Key</th>
                      <th className="px-2 py-1.5">Target (current)</th>
                      <th className="px-2 py-1.5">Source (incoming)</th>
                      <th className="px-2 py-1.5">Class</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-brand-border">
                    {hunks.filter((h) => h.category === cat).map((h) => {
                      const disabled = !canEdit || !selectable(h) || groupOf(h, hunks).some((g) => !selectable(g));
                      const why = !canEdit
                        ? "Only Business Unit admins can promote"
                        : h.classification === "backend"
                          ? "Backend / remote-state config is never promoted"
                          : !h.applicable
                            ? h.not_applicable_reason ?? "Can't be applied automatically"
                            : undefined;
                      const lines = h.source_lines ?? h.target_lines;
                      return (
                        <tr key={h.id} className={cx(sel.has(h.id) && "bg-[rgba(var(--td-glow-rgb),0.07)]")}>
                          <td className="px-2 py-1.5 align-top">
                            <span title={why}>
                              <input
                                type="checkbox"
                                className="accent-[var(--td-rail)]"
                                aria-label={`Select ${h.key}`}
                                checked={sel.has(h.id)}
                                disabled={disabled}
                                onChange={() => toggle(h)}
                              />
                            </span>
                          </td>
                          <td className="px-2 py-1.5 align-top">
                            <span className="block break-all font-mono text-[12px] text-brand-text">
                              {h.level === "file" ? `file ${h.file}` : h.key}
                            </span>
                            <span className="font-mono text-[10.5px] text-brand-muted">
                              {h.kind} · {h.file}
                              {lines ? `:${lines[0]}${lines[1] !== lines[0] ? `-${lines[1]}` : ""}` : ""}
                              {h.group && h.group !== h.key ? ` · with ${h.group}` : ""}
                            </span>
                            {overrides.get(h.id) && sel.has(h.id) && (
                              <span className="mt-0.5 block text-[11px] italic text-[var(--td-warn-ink)]">
                                reason: {overrides.get(h.id)}
                              </span>
                            )}
                          </td>
                          <td className="max-w-[260px] px-2 py-1.5 align-top"><Value v={h.target_value} /></td>
                          <td className="max-w-[260px] px-2 py-1.5 align-top"><Value v={h.source_value} /></td>
                          <td className="px-2 py-1.5 align-top"><ClassBadge h={h} /></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </section>
          ))}
        </div>
      )}

      {asking && (
        <ReasonDialog
          keys={asking.filter((g) => g.classification === "protected").map((g) => g.key)}
          initial=""
          onCancel={() => setAsking(null)}
          onConfirm={(reason) => {
            const next = new Set(sel);
            const reasons = new Map<string, string>();
            asking.forEach((g) => {
              next.add(g.id);
              if (g.classification === "protected") reasons.set(g.id, reason);
            });
            onSetHunks(next, reasons);
            setAsking(null);
          }}
        />
      )}
    </div>
  );
}

export { UnifiedDiff };
