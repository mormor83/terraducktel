import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  PROMOTION_STATUS_TONE,
  extractError,
  getPromotion,
  promotionStatusLabel,
  revertPromotion,
  type Promotion,
  type PromotionStatus,
  type StageStatus,
} from "../api/envLinks";
import { Badge, Button, Card, ConfirmDialog, SectionHeader, Skeleton, cx } from "../components/ui";
import { ENV_BASE, useIsBuAdmin } from "./Environments";

const FINAL: PromotionStatus[] = ["commit_failed", "succeeded", "partially_succeeded", "failed", "rejected"];
const POLL_MS = 4000;

function relTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return new Date(iso).toLocaleString();
}

const DOT: Record<StageStatus, string> = {
  success: "bg-[rgb(var(--td-ok-rgb))]",
  failed: "bg-[rgb(var(--td-err-rgb))]",
  running: "bg-[rgb(var(--td-run-rgb))] animate-pulse",
  warning: "bg-[rgb(var(--td-warn-rgb))]",
  skipped: "bg-[var(--td-idle-soft)]",
  pending: "bg-[var(--td-idle-soft)]",
};

const INK: Record<StageStatus, string> = {
  success: "text-[var(--td-green-ink)]",
  failed: "text-[var(--td-err-ink)]",
  running: "text-[var(--td-run-ink)]",
  warning: "text-[var(--td-warn-ink)]",
  skipped: "text-brand-muted",
  pending: "text-brand-muted",
};

type StackT = NonNullable<Promotion["stacks"]>[number];

function StageTracker({ stages }: { stages: StackT["stages"] }) {
  return (
    <ol className="flex flex-wrap items-start gap-x-1 gap-y-3" aria-label="Pipeline stages">
      {stages.map((st, i) => (
        <li key={st.key} className="flex items-start" data-testid={`stage-${st.key}`} data-status={st.status}>
          <div className="flex min-w-[84px] flex-col items-center text-center">
            <span className={cx("mb-1.5 h-3 w-3 rounded-full", DOT[st.status] ?? DOT.pending)} aria-hidden />
            <span className={cx("font-mono text-[11px] font-medium", INK[st.status] ?? INK.pending)}>{st.label}</span>
            <span className="sr-only">{st.status}</span>
            {st.detail && (
              <span className="mt-0.5 max-w-[120px] break-words text-[11px] text-brand-muted">{st.detail}</span>
            )}
          </div>
          {i < stages.length - 1 && (
            <span aria-hidden className="mt-[5px] h-px w-5 bg-brand-border sm:w-7" />
          )}
        </li>
      ))}
    </ol>
  );
}

function StackCard({ s, linkId }: { s: StackT; linkId: string }) {
  return (
    <Card className="px-5 py-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="font-display text-[15px] font-semibold text-brand-text">{s.target_stack_name ?? s.target_stack_id}</span>
        {s.target_path && <span className="truncate font-mono text-[11.5px] text-brand-muted">{s.target_path}</span>}
        {s.created_stack && <Badge tone="violet">created</Badge>}
        <span className="ml-auto flex items-center gap-3 text-[12px]">
          {s.run_status === "awaiting_approval" && s.run_id && (
            <Link to={`/runs/${s.run_id}`} className="font-medium text-[var(--td-warn-ink)] hover:underline">
              Approve in Runs →
            </Link>
          )}
          {s.run_id && (
            <Link to={`/runs/${s.run_id}`} className="font-mono text-brand-textSoft hover:underline">
              Open run
            </Link>
          )}
        </span>
      </div>
      <StageTracker stages={s.stages} />
      {s.verified_at && s.residual && (
        <div className="mt-4 border-t border-brand-border pt-3 text-[12.5px]">
          {s.residual.in_sync ? (
            <p className="text-[var(--td-green-ink)]">In sync — nothing promotable left between source and target.</p>
          ) : (
            <>
              <p className="text-[var(--td-warn-ink)]">
                {s.residual.promotable ?? 0} difference{(s.residual.promotable ?? 0) === 1 ? "" : "s"} left
                {s.residual.error ? ` (${s.residual.error})` : ""}
              </p>
              {s.residual.keys && s.residual.keys.length > 0 && (
                <ul className="mt-1 space-y-0.5">
                  {s.residual.keys.map((k) => (
                    <li key={k} className="font-mono text-[11.5px] text-brand-textSoft">{k}</li>
                  ))}
                </ul>
              )}
              <Link to={`${ENV_BASE}/${linkId}`} className="mt-1 inline-block text-brand-textSoft hover:underline">
                Back to compare →
              </Link>
            </>
          )}
        </div>
      )}
    </Card>
  );
}

export default function PromotionDetail() {
  const { id = "", pid = "" } = useParams<{ id: string; pid: string }>();
  const navigate = useNavigate();
  const isBuAdmin = useIsBuAdmin();
  const [p, setP] = useState<Promotion | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmRevert, setConfirmRevert] = useState(false);
  const [reverting, setReverting] = useState(false);
  const [revertError, setRevertError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setP(await getPromotion(pid));
      setError(null);
    } catch (e) {
      setError(extractError(e, "Failed to load promotion"));
    }
  }, [pid]);

  useEffect(() => {
    void load();
  }, [load]);

  const isFinal = !!p && FINAL.includes(p.status);
  useEffect(() => {
    if (!p || isFinal) return;
    const t = window.setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, POLL_MS);
    return () => window.clearInterval(t);
  }, [p, isFinal, load]);

  if (error && !p) {
    return (
      <div>
        <SectionHeader eyebrow="GOVERNANCE · ENVIRONMENTS" title="Promotion" />
        <p className="text-[13px] text-[var(--td-err-ink)]">{error}</p>
      </div>
    );
  }
  if (!p) return <Skeleton className="h-64" />;

  const linkId = p.link_id || id;
  const canRevert =
    isBuAdmin && isFinal && p.kind === "promote" && p.commits.length > 0 && !p.reverted_by;

  const doRevert = async () => {
    setReverting(true);
    setRevertError(null);
    try {
      const rev = await revertPromotion(p.id);
      setConfirmRevert(false);
      navigate(`${ENV_BASE}/${rev.link_id || linkId}/promotions/${rev.id}`);
    } catch (e) {
      setRevertError(extractError(e, "Revert failed"));
      setConfirmRevert(false);
    } finally {
      setReverting(false);
    }
  };

  return (
    <div>
      <SectionHeader
        eyebrow="GOVERNANCE · ENVIRONMENTS"
        title={`Promotion #${p.number}`}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <Link to={`${ENV_BASE}/${linkId}`} className="font-medium text-brand-text hover:underline">
              {p.link_name ?? "environment link"}
            </Link>
            <span>·</span>
            <span>{p.initiated_by_email ?? p.initiated_by}</span>
            <span>·</span>
            <span title={new Date(p.created_at).toLocaleString()}>{relTime(p.created_at)}</span>
          </span>
        }
        action={
          canRevert ? (
            <Button variant="danger" onClick={() => setConfirmRevert(true)} disabled={reverting}>
              Revert…
            </Button>
          ) : undefined
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Badge tone={PROMOTION_STATUS_TONE[p.status] ?? "neutral"} dot={!isFinal}>
          {promotionStatusLabel(p.status)}
        </Badge>
        {p.kind === "revert" && <Badge tone="warning">revert</Badge>}
        {p.kind === "revert" && p.reverts_promotion_id && (
          <Link to={`${ENV_BASE}/${linkId}/promotions/${p.reverts_promotion_id}`}
            className="font-mono text-[11.5px] text-brand-textSoft hover:underline">
            reverts an earlier promotion →
          </Link>
        )}
        {p.reverted_by && (
          <Link to={`${ENV_BASE}/${linkId}/promotions/${p.reverted_by.id}`}
            className="font-mono text-[11.5px] text-brand-textSoft hover:underline">
            Reverted by #{p.reverted_by.number}
          </Link>
        )}
        {p.direction === "reverse" && <Badge tone="warning">reverse · target → source</Badge>}
      </div>

      {p.reason && (
        <p className="mb-4 text-[13px] text-brand-textSoft">
          <span className="font-mono text-[10px] uppercase tracking-[1.3px] text-brand-muted">Reason </span>
          {p.reason}
        </p>
      )}

      {p.error && (
        <Card className="mb-4 border-[rgba(var(--td-err-rgb),0.35)] bg-[rgba(var(--td-err-rgb),0.06)] px-5 py-3">
          <p className="whitespace-pre-wrap font-mono text-[12px] text-[var(--td-err-ink)]">{p.error}</p>
        </Card>
      )}
      {revertError && (
        <p className="mb-4 text-[13px] text-[var(--td-err-ink)]" role="alert">{revertError}</p>
      )}

      <Card className="mb-4 px-5 py-4">
        <p className="mb-2 font-mono text-[10px] uppercase tracking-[1.3px] text-brand-muted">Commits</p>
        {p.commits.length === 0 ? (
          <p className="text-[12.5px] text-brand-muted">
            {p.status === "committing" || p.status === "pending" ? "Committing…" : "Nothing was committed."}
          </p>
        ) : (
          <ul className="space-y-1">
            {p.commits.map((c) => (
              <li key={c.sha} className="flex flex-wrap items-center gap-2 text-[12.5px]">
                <span className="font-mono text-brand-textSoft">⎇ {c.branch}</span>
                {c.web_url ? (
                  <a href={c.web_url} target="_blank" rel="noreferrer noopener"
                    className="font-mono text-brand-text hover:underline">{c.sha.slice(0, 10)}</a>
                ) : (
                  <span className="font-mono text-brand-text">{c.sha.slice(0, 10)}</span>
                )}
                <span className="text-brand-muted">{c.files.length} file{c.files.length === 1 ? "" : "s"}</span>
                <span className="truncate font-mono text-[11px] text-brand-muted">{c.repo_url}</span>
              </li>
            ))}
          </ul>
        )}
        {p.commit_message && (
          <details className="mt-3">
            <summary className="cursor-pointer font-mono text-[11px] text-brand-muted">Commit message</summary>
            <pre className="mt-2 whitespace-pre-wrap rounded-md border border-brand-border bg-brand-surface2 p-3 font-mono text-[11.5px] text-brand-textSoft">
              {p.commit_message}
            </pre>
          </details>
        )}
      </Card>

      <div className="space-y-3">
        {(p.stacks ?? []).map((s) => (
          <StackCard key={s.promotion_run_id} s={s} linkId={linkId} />
        ))}
      </div>

      <ConfirmDialog
        open={confirmRevert}
        title={`Revert promotion #${p.number}?`}
        message="Creates a revert commit on the same branch(es) and runs the normal pipeline — plan, policy checks, cost and approval — on every affected stack. Nothing is applied without approval."
        confirmLabel="Revert"
        tone="danger"
        busy={reverting}
        onCancel={() => setConfirmRevert(false)}
        onConfirm={doRevert}
      />
    </div>
  );
}
