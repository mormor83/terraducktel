// Governance › Environments › <link> — the Compare screen.
//
// Left: every pair of the link (status, mini summary, bulk checkbox). Right:
// the selected pair's Config (semantic + raw diff, create preview), State
// (live inventory diff) and History tabs. A sticky bar collects the selection
// and opens the promotion preview drawer; promotion itself always goes through
// the normal gated pipeline (the drawer only commits + creates runs).
//
// Compares are computed server-side in the background: GET returns 202 while
// computing, so this page polls the selected pair (2 s) and the pair list (5 s)
// only while something is still in flight.

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import {
  PAIR_STATUS_LABEL,
  deleteEnvLink,
  extractError,
  getCompare,
  getEnvLink,
  listEnvPairs,
  pairSummaryText,
  recompare,
  type CompareResult,
  type Direction,
  type EnvLink,
  type EnvPair,
  type PairStatus,
  type SideRef,
} from "../api/envLinks";
import { ConfigTab } from "../components/env/compare/ConfigTab";
import { HistoryTab } from "../components/env/compare/HistoryTab";
import { PairList } from "../components/env/compare/PairList";
import { PromoteDrawer } from "../components/env/compare/PromoteDrawer";
import {
  allPromotable,
  countSelection,
  toSelection,
  type Overrides,
  type Picks,
} from "../components/env/compare/selection";
import { StateTab } from "../components/env/compare/StateTab";
import { Badge, Button, Card, ConfirmDialog, DriftBadge, Input, SectionHeader, Skeleton, cx } from "../components/ui";
import { ENV_BASE, NodeSide, useIsBuAdmin } from "./Environments";

const CHIP_ORDER: PairStatus[] = [
  "diverged",
  "missing_in_target",
  "in_sync",
  "not_compared",
  "missing_in_source",
  "excluded",
];
type Tab = "config" | "state" | "history";

const shortSha = (sha: string | null | undefined) => (sha ? sha.slice(0, 7) : "—");

function SideRefLine({ label, ref_ }: { label: string; ref_: SideRef | undefined }) {
  if (!ref_) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[11px] text-brand-muted">
      <span className="uppercase tracking-[1.2px]">{label}</span>
      {ref_.branch && (
        <span className="text-brand-textSoft">
          ⎇ {ref_.branch} @ {shortSha(ref_.commit)}
        </span>
      )}
      {ref_.state_serial !== null && ref_.state_serial !== undefined && <span>state #{ref_.state_serial}</span>}
      {ref_.drift_status && <DriftBadge status={ref_.drift_status} />}
    </div>
  );
}

export default function EnvLinkDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const isBuAdmin = useIsBuAdmin();

  const [link, setLink] = useState<EnvLink | null>(null);
  const [pairs, setPairs] = useState<EnvPair[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [direction, setDirection] = useState<Direction>("forward");
  const [confirmFlip, setConfirmFlip] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("config");
  const [compares, setCompares] = useState<Record<string, CompareResult>>({});
  const [picks, setPicks] = useState<Picks>(new Map());
  const [overrides, setOverrides] = useState<Overrides>(new Map());
  const [pendingBulk, setPendingBulk] = useState<Set<string>>(new Set());
  const [statusFilter, setStatusFilter] = useState<Set<PairStatus>>(new Set());
  const [query, setQuery] = useState("");
  const [drawer, setDrawer] = useState(false);
  const [recomparing, setRecomparing] = useState(false);

  const cKey = useCallback((pairId: string) => `${pairId}:${direction}`, [direction]);

  const loadPairs = useCallback(async () => {
    if (!id) return;
    try {
      const p = await listEnvPairs(id);
      setPairs(p.items);
    } catch (e) {
      setError(extractError(e, "Failed to load pairs"));
    }
  }, [id]);

  useEffect(() => {
    if (!id) return;
    Promise.all([getEnvLink(id), listEnvPairs(id)])
      .then(([l, p]) => {
        setLink(l);
        setPairs(p.items);
        const first = p.items.find((x) => x.status === "diverged") ?? p.items.find((x) => x.status !== "excluded") ?? p.items[0];
        if (first?.id) setSelectedId(first.id);
      })
      .catch((e) => setError(extractError(e, "Failed to load environment link")));
  }, [id]);

  // Enqueue compares for stale pairs once per direction (server skips fresh ones).
  useEffect(() => {
    if (!id) return;
    recompare(id, { direction }).catch(() => {});
  }, [id, direction]);

  const fetchCompare = useCallback(
    async (pairId: string) => {
      try {
        const c = await getCompare(pairId, direction);
        setCompares((prev) => ({ ...prev, [`${pairId}:${direction}`]: c }));
        return c;
      } catch (e) {
        setError(extractError(e, "Failed to load compare"));
        return null;
      }
    },
    [direction],
  );

  // Initial fetch for the selected pair.
  useEffect(() => {
    if (selectedId && !compares[cKey(selectedId)]) fetchCompare(selectedId);
  }, [selectedId, cKey, compares, fetchCompare]);

  // Poll in-flight compares (selected + bulk-pending).
  const inflight = useMemo(() => {
    const ids = new Set<string>();
    const consider = [selectedId, ...pendingBulk].filter(Boolean) as string[];
    for (const pid of consider) {
      const c = compares[cKey(pid)];
      if (!c || c.status === "computing") ids.add(pid);
    }
    return [...ids];
  }, [selectedId, pendingBulk, compares, cKey]);

  useEffect(() => {
    if (inflight.length === 0) return;
    const t = window.setInterval(() => inflight.forEach((pid) => fetchCompare(pid)), 2000);
    return () => window.clearInterval(t);
  }, [inflight, fetchCompare]);

  const anyNotCompared = !!pairs?.some((p) => p.status === "not_compared");
  useEffect(() => {
    if (!anyNotCompared && inflight.length === 0) return;
    const t = window.setInterval(loadPairs, 5000);
    return () => window.clearInterval(t);
  }, [anyNotCompared, inflight.length, loadPairs]);

  // Bulk-selected pairs pick up all promotable hunks once their compare is ready.
  useEffect(() => {
    if (pendingBulk.size === 0) return;
    const done: string[] = [];
    const updates: [string, string[]][] = [];
    for (const pid of pendingBulk) {
      const c = compares[cKey(pid)];
      if (c && c.status === "ready") {
        done.push(pid);
        updates.push([pid, allPromotable(c)]);
      }
    }
    if (!done.length) return;
    setPicks((prev) => {
      const next = new Map(prev);
      for (const [pid, ids] of updates) if (ids.length) next.set(pid, { hunks: new Set(ids), create: false });
      return next;
    });
    setPendingBulk((prev) => {
      const next = new Set(prev);
      done.forEach((d) => next.delete(d));
      return next;
    });
  }, [pendingBulk, compares, cKey]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (pairs ?? []).filter(
      (p) =>
        (statusFilter.size === 0 || statusFilter.has(p.status)) &&
        (!q ||
          `${p.relative_path} ${p.source_rel ?? ""} ${p.target_rel ?? ""} ${p.source_stack_name ?? ""} ${p.target_stack_name ?? ""}`
            .toLowerCase()
            .includes(q)),
    );
  }, [pairs, statusFilter, query]);

  const counts = useMemo(() => {
    const c: Partial<Record<PairStatus, number>> = {};
    for (const p of pairs ?? []) c[p.status] = (c[p.status] ?? 0) + 1;
    return c;
  }, [pairs]);

  const canCreate = (p: EnvPair) => direction === "forward" && p.status === "missing_in_target";
  const canSelect = (p: EnvPair) =>
    isBuAdmin && !!p.id && (canCreate(p) || (["diverged", "not_compared", "in_sync"] as PairStatus[]).includes(p.status) && !!p.source_stack_id && !!p.target_stack_id);

  const togglePair = (p: EnvPair) => {
    if (!p.id) return;
    const current = picks.get(p.id);
    if (current && (current.hunks.size > 0 || current.create)) {
      setPicks((prev) => {
        const next = new Map(prev);
        next.delete(p.id!);
        return next;
      });
      setPendingBulk((prev) => {
        const next = new Set(prev);
        next.delete(p.id!);
        return next;
      });
      return;
    }
    if (canCreate(p)) {
      setPicks((prev) => new Map(prev).set(p.id!, { hunks: new Set(), create: true }));
      return;
    }
    const c = compares[cKey(p.id)];
    if (c && c.status === "ready") {
      const ids = allPromotable(c);
      if (ids.length) setPicks((prev) => new Map(prev).set(p.id!, { hunks: new Set(ids), create: false }));
      return;
    }
    setPendingBulk((prev) => new Set(prev).add(p.id!));
    fetchCompare(p.id);
  };

  const setHunks = (pairId: string, ids: Set<string>, reasons?: Map<string, string>) => {
    setPicks((prev) => {
      const next = new Map(prev);
      if (ids.size === 0 && !prev.get(pairId)?.create) next.delete(pairId);
      else next.set(pairId, { hunks: ids, create: prev.get(pairId)?.create ?? false });
      return next;
    });
    if (reasons?.size) {
      setOverrides((prev) => {
        const next = new Map(prev);
        reasons.forEach((r, h) => next.set(h, r));
        return next;
      });
    }
  };

  const setCreate = (pairId: string, on: boolean) =>
    setPicks((prev) => {
      const next = new Map(prev);
      if (on) next.set(pairId, { hunks: new Set(), create: true });
      else next.delete(pairId);
      return next;
    });

  const flip = () => {
    setDirection((d) => (d === "forward" ? "reverse" : "forward"));
    setPicks(new Map());
    setOverrides(new Map());
    setPendingBulk(new Set());
  };

  const doRecompare = async () => {
    if (!id) return;
    setRecomparing(true);
    try {
      await recompare(id, { force: true, direction });
      setCompares({});
      await loadPairs();
    } catch (e) {
      setError(extractError(e, "Recompare failed"));
    } finally {
      setRecomparing(false);
    }
  };

  const toggleStatus = (s: PairStatus) =>
    setStatusFilter((prev) => {
      const next = new Set(prev);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      return next;
    });

  if (error && !link) {
    return (
      <div>
        <SectionHeader eyebrow="GOVERNANCE · ENVIRONMENTS" title="Environment link" />
        <p className="text-[13px] text-[var(--td-err-ink)]">{error}</p>
      </div>
    );
  }
  if (!link || !pairs) return <Skeleton className="h-64" />;

  const selected = pairs.find((p) => p.id === selectedId) ?? null;
  const compare = selected?.id ? compares[cKey(selected.id)] : undefined;
  const { changes, stacks } = countSelection(picks);
  const reversed = direction === "reverse";
  const fromNode = reversed ? link.target_node : link.source_node;
  const toNode = reversed ? link.source_node : link.target_node;
  const fromAcct = reversed ? link.target_account_id : link.source_account_id;
  const toAcct = reversed ? link.source_account_id : link.target_account_id;
  const selection = toSelection(direction, picks, overrides);
  const defaultReason = [...new Set(selection.protected_overrides.map((o) => o.reason))].join("; ");
  const promoteTitle = !isBuAdmin
    ? "Only Business Unit admins can promote"
    : changes === 0
      ? "Select changes to promote"
      : undefined;

  return (
    <div className="pb-4">
      <SectionHeader
        eyebrow="GOVERNANCE · ENVIRONMENTS"
        title={link.name}
        subtitle={pairSummaryText(link.pair_summary)}
        action={
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={doRecompare} disabled={recomparing}>
              {recomparing ? "Recomparing…" : "Recompare"}
            </Button>
            {isBuAdmin && (
              <>
                <Button variant="secondary" onClick={() => navigate(`${ENV_BASE}/${link.id}/edit`)}>Edit link</Button>
                <Button variant="danger" onClick={() => setConfirmDelete(true)}>Delete</Button>
              </>
            )}
          </div>
        }
      />

      {reversed && (
        <p className="mb-3 rounded-md border border-[rgba(var(--td-warn-rgb),0.35)] bg-[rgba(var(--td-warn-rgb),0.1)] px-3 py-2 text-[13px] text-[var(--td-warn-ink)]">
          Reversed: comparing and promoting <b>target → source</b> for this session. The link itself keeps its direction.
        </p>
      )}
      {error && <p className="mb-3 text-[13px] text-[var(--td-err-ink)]">{error}</p>}

      <Card className="mb-4 px-5 py-4">
        <div className="flex flex-wrap items-center gap-4">
          <div className="min-w-0 flex-1">
            <p className="mb-1 font-mono text-[10px] uppercase tracking-[1.3px] text-brand-muted">From</p>
            <NodeSide accountId={fromAcct} path={fromNode.path} level={link.level} />
          </div>
          <div className="flex flex-col items-center gap-1">
            <span className="font-mono text-[18px] text-brand-muted" aria-label="promotes to">→</span>
            <Button variant="ghost" size="sm" onClick={() => setConfirmFlip(true)} aria-pressed={reversed}>
              ⇄ Flip
            </Button>
          </div>
          <div className="min-w-0 flex-1">
            <p className="mb-1 font-mono text-[10px] uppercase tracking-[1.3px] text-brand-muted">To</p>
            <NodeSide accountId={toAcct} path={toNode.path} level={link.level} />
          </div>
        </div>
        {compare?.refs && (
          <div className="mt-3 space-y-1 border-t border-brand-border pt-3">
            <SideRefLine label="from" ref_={compare.refs.source} />
            <SideRefLine label="to" ref_={compare.refs.target} />
            {compare.computed_at && (
              <p className="font-mono text-[10.5px] text-brand-muted">
                compared {new Date(compare.computed_at).toLocaleString()}
                {compare.stale && " · refreshing…"}
              </p>
            )}
          </div>
        )}
        {link.warnings.length > 0 && (
          <ul className="mt-3 space-y-1 border-t border-brand-border pt-3">
            {link.warnings.map((w) => (
              <li key={w} className="text-[12.5px] text-[var(--td-warn-ink)]">⚠ {w}</li>
            ))}
          </ul>
        )}
      </Card>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        {CHIP_ORDER.filter((s) => (counts[s] ?? 0) > 0).map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => toggleStatus(s)}
            aria-pressed={statusFilter.has(s)}
            className={cx(
              "rounded-full border px-3 py-1 font-mono text-[11px] transition-colors",
              statusFilter.has(s)
                ? "border-brand-borderStrong bg-[rgba(var(--td-glow-rgb),0.12)] text-brand-text"
                : "border-brand-border text-brand-muted hover:text-brand-text",
            )}
          >
            {PAIR_STATUS_LABEL[s]} · {counts[s]}
          </button>
        ))}
        <div className="ml-auto w-full max-w-xs">
          <Input placeholder="Filter pairs…" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Filter pairs" />
        </div>
      </div>

      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[minmax(260px,340px)_1fr]">
        <Card className="max-h-[70vh] overflow-y-auto">
          <PairList
            pairs={shown}
            selectedId={selectedId}
            picks={picks}
            loadingIds={new Set(inflight)}
            canSelect={canSelect}
            onSelect={(p) => {
              if (p.id) setSelectedId(p.id);
            }}
            onToggle={togglePair}
          />
        </Card>

        <Card className="min-w-0 p-4">
          {!selected ? (
            <p className="text-[13px] text-brand-muted">Select a pair to see what differs.</p>
          ) : (
            <>
              <div className="mb-3 flex flex-wrap items-center gap-3">
                <h3 className="min-w-0 flex-1 truncate font-mono text-[13.5px] text-brand-text">{selected.relative_path || "."}</h3>
                <div className="inline-flex rounded-md border border-brand-border p-0.5" role="tablist" aria-label="Pair detail">
                  {(["config", "state", "history"] as Tab[]).map((t) => (
                    <button
                      key={t}
                      type="button"
                      role="tab"
                      aria-selected={tab === t}
                      onClick={() => setTab(t)}
                      className={cx(
                        "rounded px-3 py-1 text-[12px] font-medium capitalize transition-colors",
                        tab === t ? "bg-[rgba(var(--td-glow-rgb),0.18)] text-brand-text" : "text-brand-muted hover:text-brand-text",
                      )}
                    >
                      {t}
                    </button>
                  ))}
                </div>
              </div>
              {tab === "config" && (
                <ConfigTab
                  pair={selected}
                  compare={compare}
                  direction={direction}
                  pick={selected.id ? picks.get(selected.id) : undefined}
                  overrides={overrides}
                  canEdit={isBuAdmin}
                  onSetHunks={(ids, reasons) => selected.id && setHunks(selected.id, ids, reasons)}
                  onSetCreate={(on) => selected.id && setCreate(selected.id, on)}
                />
              )}
              {tab === "state" && (
                <StateTab
                  pair={selected}
                  compare={compare}
                  onReloaded={() => {
                    if (!selected.id) return;
                    setCompares((prev) => {
                      const next = { ...prev };
                      delete next[cKey(selected.id!)];
                      return next;
                    });
                  }}
                />
              )}
              {tab === "history" && selected.id && <HistoryTab pairId={selected.id} linkId={link.id} base={ENV_BASE} />}
            </>
          )}
        </Card>
      </div>

      <div className="sticky bottom-0 z-20 mt-4">
        <Card className="flex flex-wrap items-center gap-3 px-5 py-3 shadow-lg">
          <span className="text-[13px] text-brand-text">
            <b>{changes}</b> change{changes === 1 ? "" : "s"} in <b>{stacks}</b> stack{stacks === 1 ? "" : "s"} selected
          </span>
          {pendingBulk.size > 0 && (
            <span className="font-mono text-[11px] text-brand-muted">loading {pendingBulk.size} compare(s)…</span>
          )}
          {selection.protected_overrides.length > 0 && (
            <Badge tone="warning">{selection.protected_overrides.length} protected</Badge>
          )}
          {changes > 0 && (
            <Button variant="ghost" size="sm" onClick={() => { setPicks(new Map()); setOverrides(new Map()); }}>
              Clear
            </Button>
          )}
          <span className="ml-auto" title={promoteTitle}>
            <Button disabled={!isBuAdmin || changes === 0} onClick={() => setDrawer(true)}>
              Promote →
            </Button>
          </span>
        </Card>
      </div>

      {drawer && (
        <PromoteDrawer
          linkId={link.id}
          selection={selection}
          defaultReason={defaultReason}
          onClose={() => setDrawer(false)}
          onDone={(p) => navigate(`${ENV_BASE}/${link.id}/promotions/${p.id}`)}
        />
      )}

      <ConfirmDialog
        open={confirmFlip}
        title={reversed ? "Back to the link's direction?" : "Flip direction?"}
        message={
          reversed
            ? "Compare and promote source → target again. Your current selection is cleared."
            : `You are about to compare/promote ${link.target_node.path} → ${link.source_node.path}. Promotions in this direction are one-off; the link keeps its direction. Your current selection is cleared.`
        }
        confirmLabel={reversed ? "Back to forward" : "Flip"}
        tone="warning"
        onCancel={() => setConfirmFlip(false)}
        onConfirm={() => {
          setConfirmFlip(false);
          flip();
        }}
      />

      <ConfirmDialog
        open={confirmDelete}
        title={`Delete "${link.name}"?`}
        message="Removes the link, its pairing and its promotion history. No stacks, runs or state are touched."
        confirmLabel="Delete link"
        tone="danger"
        onCancel={() => setConfirmDelete(false)}
        onConfirm={async () => {
          try {
            await deleteEnvLink(link.id);
            navigate(ENV_BASE);
          } catch (e) {
            setError(extractError(e, "Failed to delete link"));
            setConfirmDelete(false);
          }
        }}
      />
    </div>
  );
}
