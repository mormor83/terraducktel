import { useState } from "react";
import { Link } from "react-router-dom";

import { extractError, refreshStateRun, reloadState, type CompareResult, type EnvPair } from "../../../api/envLinks";
import { hasMinRole, useCurrentUser } from "../../../hooks/useAuth";
import { Badge, Button, ConfirmDialog, EmptyState, Skeleton, cx } from "../../ui";

type Side = "source" | "target";

function Meta({ label, meta, err }: { label: string; meta?: { serial: number | null; resource_count: number; terraform_version: string | null }; err?: string | null }) {
  return (
    <div className="min-w-0 flex-1 rounded-md border border-brand-border px-3 py-2">
      <p className="font-mono text-[10px] uppercase tracking-[1.3px] text-brand-muted">{label}</p>
      {err ? (
        <p className="mt-1 text-[12px] text-[var(--td-err-ink)]">{err}</p>
      ) : meta ? (
        <p className="mt-1 font-mono text-[11.5px] text-brand-textSoft">
          serial {meta.serial ?? "—"} · {meta.resource_count} resources · tf {meta.terraform_version ?? "—"}
        </p>
      ) : (
        <p className="mt-1 text-[12px] text-brand-muted">No state</p>
      )}
    </div>
  );
}

export function StateTab({
  pair,
  compare,
  onReloaded,
}: {
  pair: EnvPair;
  compare: CompareResult | undefined;
  onReloaded: () => void;
}) {
  const user = useCurrentUser();
  const canRefresh = hasMinRole(user, "operator");
  const [busy, setBusy] = useState<Side | null>(null);
  const [confirm, setConfirm] = useState<Side | null>(null);
  const [runLink, setRunLink] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());

  if (!pair.source_stack_id || !pair.target_stack_id) {
    return <EmptyState title="Nothing to compare" description="Live state is compared once both sides have a stack." />;
  }
  if (!compare || (!compare.refs && compare.status === "computing")) return <Skeleton className="h-40" />;

  const sd = compare.state_diff;
  const refs = compare.refs;

  const reload = async (side: Side) => {
    setBusy(side);
    setError(null);
    try {
      await reloadState(pair.id!, side);
      onReloaded();
    } catch (e) {
      setError(extractError(e, "Failed to reload state"));
    } finally {
      setBusy(null);
    }
  };

  const actions = (
    <div className="flex flex-wrap items-center gap-2">
      {(["source", "target"] as Side[]).map((side) => (
        <Button key={side} variant="secondary" size="sm" disabled={busy !== null} onClick={() => reload(side)}>
          {busy === side ? "Reloading…" : `Reload ${side} state`}
        </Button>
      ))}
      {canRefresh &&
        (["source", "target"] as Side[]).map((side) => (
          <Button key={`r-${side}`} variant="ghost" size="sm" onClick={() => setConfirm(side)}>
            Refresh {side} from cloud…
          </Button>
        ))}
    </div>
  );

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-[12px] text-brand-muted">Read-only — promotion always goes through config.</p>
        {actions}
      </div>
      {error && <p className="mb-2 text-[12.5px] text-[var(--td-err-ink)]">{error}</p>}
      {runLink && (
        <p className="mb-2 text-[12.5px] text-brand-textSoft">
          Refresh-only run queued — it waits for approval like any other run.{" "}
          <Link className="underline" to={runLink}>Open run</Link>
        </p>
      )}
      <div className="mb-3 flex flex-wrap gap-2">
        <Meta label="Source" meta={sd?.source_meta} err={refs?.source.state_error} />
        <Meta label="Target" meta={sd?.target_meta} err={refs?.target.state_error} />
      </div>

      {!sd ? (
        <EmptyState title="No state to compare" description={refs?.source.state_error || refs?.target.state_error || "Neither side has state yet."} />
      ) : sd.source_meta.resource_count === 0 && sd.target_meta.resource_count === 0 ? (
        <EmptyState title="Both states are empty" description="Nothing has been applied on either side yet." />
      ) : (
        <div className="space-y-4">
          <section>
            <h4 className="mb-1.5 font-mono text-[10.5px] uppercase tracking-[1.3px] text-brand-muted">Resource types</h4>
            <div className="overflow-x-auto rounded-md border border-brand-border">
              <table className="w-full text-left text-[12.5px]">
                <thead className="bg-brand-surface2 font-mono text-[10px] uppercase tracking-[1px] text-brand-muted">
                  <tr><th className="px-2 py-1.5">Type</th><th className="px-2 py-1.5">Source</th><th className="px-2 py-1.5">Target</th></tr>
                </thead>
                <tbody className="divide-y divide-brand-border font-mono text-[12px]">
                  {sd.type_counts.map((t) => (
                    <tr key={t.type} className={cx(t.source !== t.target && "bg-[rgba(var(--td-warn-rgb),0.08)]")}>
                      <td className="px-2 py-1 text-brand-text">{t.type}</td>
                      <td className="px-2 py-1">{t.source}</td>
                      <td className="px-2 py-1">{t.target}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {([["Only in source", sd.only_in_source], ["Only in target", sd.only_in_target]] as const).map(([title, list]) => (
              <section key={title}>
                <h4 className="mb-1.5 flex items-center gap-2 font-mono text-[10.5px] uppercase tracking-[1.3px] text-brand-muted">
                  {title} <Badge tone="neutral">{list.length}</Badge>
                </h4>
                <ul className="max-h-48 space-y-0.5 overflow-auto">
                  {list.length === 0 && <li className="text-[12px] italic text-brand-muted">none</li>}
                  {list.map((a) => (
                    <li key={a} className="truncate font-mono text-[11.5px] text-brand-textSoft" title={a}>{a}</li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
          <section>
            <h4 className="mb-1.5 flex items-center gap-2 font-mono text-[10.5px] uppercase tracking-[1.3px] text-brand-muted">
              Differing <Badge tone="warning">{sd.differing.length}</Badge>
              <span className="normal-case tracking-normal">· {sd.identical} identical of {sd.in_both} in both</span>
            </h4>
            <ul className="space-y-1">
              {sd.differing.map((d) => {
                const isOpen = open.has(d.address);
                return (
                  <li key={d.address} className="rounded-md border border-brand-border">
                    <button
                      type="button"
                      aria-expanded={isOpen}
                      onClick={() => setOpen((p) => { const n = new Set(p); n.has(d.address) ? n.delete(d.address) : n.add(d.address); return n; })}
                      className="flex w-full items-center gap-2 px-3 py-1.5 text-left"
                    >
                      <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-brand-text">{isOpen ? "▾" : "▸"} {d.address}</span>
                      <span className="font-mono text-[11px] text-brand-muted">{d.attributes.length} attr</span>
                    </button>
                    {isOpen && (
                      <table className="w-full border-t border-brand-border text-left font-mono text-[11.5px]">
                        <tbody className="divide-y divide-brand-border">
                          {d.attributes.map((a) => (
                            <tr key={a.key}>
                              <td className="px-3 py-1 text-brand-text">{a.key}</td>
                              <td className="break-all px-3 py-1 text-[var(--td-green-ink)]">{a.source ?? "—"}</td>
                              <td className="break-all px-3 py-1 text-[var(--td-err-ink)]">{a.target ?? "—"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        </div>
      )}

      <ConfirmDialog
        open={confirm !== null}
        title={`Refresh ${confirm ?? ""} state from the cloud?`}
        message="This queues a refresh-only run (terraform plan -refresh-only). It changes no infrastructure, but it rewrites state, so it waits for approval like any other run."
        confirmLabel="Queue refresh run"
        tone="warning"
        onCancel={() => setConfirm(null)}
        onConfirm={async () => {
          const side = confirm!;
          setConfirm(null);
          try {
            const r = await refreshStateRun(pair.id!, side);
            setRunLink(`/runs/${r.run_id}`);
          } catch (e) {
            setError(extractError(e, "Failed to queue refresh run"));
          }
        }}
      />
    </div>
  );
}
