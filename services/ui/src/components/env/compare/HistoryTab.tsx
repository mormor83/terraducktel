import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import {
  PROMOTION_STATUS_TONE,
  extractError,
  getPairHistory,
  promotionStatusLabel,
} from "../../../api/envLinks";
import { Badge, EmptyState, RunStatusBadge, Skeleton } from "../../ui";

type History = Awaited<ReturnType<typeof getPairHistory>>;

const when = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString() : "—");

export function HistoryTab({ pairId, linkId, base }: { pairId: string; linkId: string; base: string }) {
  const [data, setData] = useState<History | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    getPairHistory(pairId)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(extractError(e, "Failed to load history")));
    return () => {
      alive = false;
    };
  }, [pairId]);

  if (error) return <p className="text-[13px] text-[var(--td-err-ink)]">{error}</p>;
  if (!data) return <Skeleton className="h-32" />;
  if (data.promotions.length === 0 && data.runs.length === 0) {
    return <EmptyState title="No history yet" description="Promotions and runs that touch this pair show up here." />;
  }
  return (
    <div className="space-y-5">
      <section>
        <h4 className="mb-1.5 font-mono text-[10.5px] uppercase tracking-[1.3px] text-brand-muted">Promotions</h4>
        {data.promotions.length === 0 ? (
          <p className="text-[12px] italic text-brand-muted">none</p>
        ) : (
          <ul className="divide-y divide-brand-border rounded-md border border-brand-border">
            {data.promotions.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-3 px-3 py-2">
                <Link to={`${base}/${linkId}/promotions/${p.id}`} className="font-mono text-[12.5px] text-brand-text hover:underline">
                  #{p.number}
                </Link>
                {p.kind === "revert" && <Badge tone="violet">revert</Badge>}
                {p.direction === "reverse" && <Badge tone="warning">reverse</Badge>}
                <Badge tone={PROMOTION_STATUS_TONE[p.status]}>{promotionStatusLabel(p.status)}</Badge>
                <span className="text-[12px] text-brand-muted">{p.initiated_by_email ?? p.initiated_by}</span>
                <span className="ml-auto font-mono text-[11px] text-brand-muted">{when(p.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section>
        <h4 className="mb-1.5 font-mono text-[10.5px] uppercase tracking-[1.3px] text-brand-muted">Recent runs</h4>
        {data.runs.length === 0 ? (
          <p className="text-[12px] italic text-brand-muted">none</p>
        ) : (
          <ul className="divide-y divide-brand-border rounded-md border border-brand-border">
            {data.runs.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center gap-3 px-3 py-2">
                <Link to={`/runs/${r.id}`} className="font-mono text-[12px] text-brand-text hover:underline">
                  {r.id.slice(0, 8)}
                </Link>
                <Badge tone="neutral">{r.side}</Badge>
                <span className="font-mono text-[11.5px] text-brand-textSoft">{r.command}</span>
                <RunStatusBadge status={r.status} />
                {r.promotion_id && <Badge tone="violet">promotion</Badge>}
                <span className="ml-auto font-mono text-[11px] text-brand-muted">{when(r.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
