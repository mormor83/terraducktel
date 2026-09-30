import { useEffect, useRef, useState } from "react";

import {
  createPromotion,
  extractError,
  previewPromotion,
  type Promotion,
  type PromotionPreview,
  type PromotionSelection,
} from "../../../api/envLinks";
import { Badge, Button, Skeleton } from "../../ui";
import { UnifiedDiff } from "./RawDiff";

function serverBlockers(e: unknown): string[] | null {
  const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (detail && typeof detail === "object" && Array.isArray((detail as { blockers?: unknown }).blockers)) {
    return (detail as { blockers: string[] }).blockers;
  }
  return null;
}

const repoName = (url: string) => url.replace(/^[a-z]+:\/\/[^/]*\//, "").replace(/\.git$/, "") || url;

export function PromoteDrawer({
  linkId,
  selection,
  defaultReason,
  onClose,
  onDone,
}: {
  linkId: string;
  selection: PromotionSelection;
  defaultReason: string;
  onClose: () => void;
  onDone: (p: Promotion) => void;
}) {
  const [preview, setPreview] = useState<PromotionPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [reason, setReason] = useState(defaultReason);
  const [submitting, setSubmitting] = useState(false);
  const [refused, setRefused] = useState<string[] | null>(null);
  const [openFiles, setOpenFiles] = useState<Set<string>>(new Set());
  const submitted = useRef(false);
  const needReason = selection.protected_overrides.length > 0;

  useEffect(() => {
    let alive = true;
    previewPromotion(linkId, selection)
      .then((p) => {
        if (!alive) return;
        setPreview(p);
        setMessage(p.commit_message);
      })
      .catch((e) => alive && setError(extractError(e, "Preview failed")));
    return () => {
      alive = false;
    };
    // The selection is fixed for the drawer's lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !submitting && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, submitting]);

  const blockers = [...(preview?.blockers ?? []), ...(refused ?? [])];
  const canConfirm = !!preview && blockers.length === 0 && !submitting && (!needReason || !!reason.trim());

  const confirm = async () => {
    if (!canConfirm || submitted.current) return;
    submitted.current = true;
    setSubmitting(true);
    setError(null);
    try {
      const promo = await createPromotion(linkId, {
        ...selection,
        commit_message: message,
        reason: reason.trim() || undefined,
      });
      onDone(promo);
    } catch (e) {
      submitted.current = false;
      const b = serverBlockers(e);
      if (b) setRefused(b);
      else setError(extractError(e, "Promotion failed"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" role="dialog" aria-modal="true" aria-label="Promotion preview">
      <button type="button" aria-label="Close" className="flex-1 cursor-default" onClick={() => !submitting && onClose()} />
      <div className="flex h-full w-full max-w-[720px] flex-col border-l border-brand-border bg-brand-surface shadow-2xl">
        <div className="flex items-center justify-between border-b border-brand-border px-5 py-4">
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[1.4px] text-[var(--td-green-ink)]">Promotion preview</p>
            <h2 className="font-display text-[19px] font-semibold text-brand-text">
              {selection.direction === "reverse" ? "Promote in reverse" : "Promote to target"}
            </h2>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose} disabled={submitting}>✕</Button>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
          {error && <p className="text-[13px] text-[var(--td-err-ink)]">{error}</p>}
          {!preview && !error && (
            <div className="space-y-2" aria-busy="true">
              <p className="font-mono text-[11px] text-brand-muted">Re-reading both sides at their current commits…</p>
              <Skeleton className="h-16" />
              <Skeleton className="h-40" />
            </div>
          )}
          {preview && (
            <>
              {blockers.length > 0 && (
                <section aria-label="Blockers" className="rounded-md border border-[rgba(var(--td-err-rgb),0.3)] bg-[rgba(var(--td-err-rgb),0.07)] p-3">
                  <p className="mb-1 font-mono text-[10.5px] uppercase tracking-[1.3px] text-[var(--td-err-ink)]">Blocked</p>
                  <ul className="list-disc space-y-0.5 pl-4 text-[12.5px] text-[var(--td-err-ink)]">
                    {blockers.map((b) => <li key={b}>{b}</li>)}
                  </ul>
                </section>
              )}
              {preview.warnings.length > 0 && (
                <section aria-label="Warnings" className="rounded-md border border-[rgba(var(--td-warn-rgb),0.3)] bg-[rgba(var(--td-warn-rgb),0.07)] p-3">
                  <ul className="list-disc space-y-0.5 pl-4 text-[12.5px] text-[var(--td-warn-ink)]">
                    {preview.warnings.map((w) => <li key={w}>{w}</li>)}
                  </ul>
                </section>
              )}
              <section>
                <p className="text-[13px] text-brand-text">
                  {preview.affected_stacks} stack{preview.affected_stacks === 1 ? "" : "s"} ·{" "}
                  {preview.commits.map((c) => `1 commit on ${c.branch} in ${repoName(c.repo_url)}`).join(" · ") || "no commits"}
                </p>
                <p className="mt-0.5 text-[12px] text-brand-muted">
                  Each affected stack then gets an ordinary apply run: plan → Checkov → OPA → cost → approval → apply.
                </p>
              </section>
              <ul className="space-y-2">
                {preview.changes.map((c) => (
                  <li key={c.pair_id} className="rounded-md border border-brand-border">
                    <div className="flex flex-wrap items-center gap-2 border-b border-brand-border px-3 py-2">
                      <span className="font-mono text-[12.5px] text-brand-text">{c.target_path}</span>
                      {c.create && <Badge tone="violet">create</Badge>}
                      {c.branch && <span className="font-mono text-[11px] text-brand-muted">⎇ {c.branch}</span>}
                      <span className="ml-auto font-mono text-[11px] text-brand-muted">
                        {c.create ? `${c.files.length} files` : `${c.hunks.length} change${c.hunks.length === 1 ? "" : "s"}`}
                      </span>
                    </div>
                    <ul>
                      {c.files.map((f) => {
                        const k = `${c.pair_id}:${f.path}`;
                        const open = openFiles.has(k);
                        return (
                          <li key={k} className="border-b border-brand-border last:border-b-0">
                            <button
                              type="button"
                              aria-expanded={open}
                              onClick={() => setOpenFiles((p) => { const n = new Set(p); n.has(k) ? n.delete(k) : n.add(k); return n; })}
                              className="flex w-full items-center gap-2 px-3 py-1.5 text-left font-mono text-[11.5px] text-brand-textSoft"
                            >
                              {open ? "▾" : "▸"} {f.path}
                              <Badge tone={f.status === "added" ? "success" : f.status === "removed" ? "danger" : "warning"} className="ml-auto">
                                {f.status}
                              </Badge>
                            </button>
                            {open && <div className="px-2 pb-2"><UnifiedDiff text={f.unified} /></div>}
                          </li>
                        );
                      })}
                    </ul>
                  </li>
                ))}
              </ul>
              <section>
                <label htmlFor="promo-message" className="mb-1 block font-mono text-[10px] uppercase tracking-[1.3px] text-brand-muted">
                  Commit message
                </label>
                <textarea
                  id="promo-message"
                  rows={6}
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  className="w-full rounded-md border border-brand-border bg-brand-surface p-2 font-mono text-[12px] text-brand-text"
                />
                <p className="mt-0.5 font-mono text-[10.5px] text-brand-muted">
                  TDT appends a Terraducktel-Promotion trailer so webhooks don't re-plan this push.
                </p>
              </section>
              <section>
                <label htmlFor="promo-reason" className="mb-1 block font-mono text-[10px] uppercase tracking-[1.3px] text-brand-muted">
                  Reason {needReason ? "(required — protected values included)" : "(optional)"}
                </label>
                <textarea
                  id="promo-reason"
                  rows={2}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  className="w-full rounded-md border border-brand-border bg-brand-surface p-2 text-[12.5px] text-brand-text"
                />
              </section>
            </>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-brand-border px-5 py-3">
          <Button variant="ghost" onClick={onClose} disabled={submitting}>Cancel</Button>
          <Button onClick={confirm} disabled={!canConfirm}>
            {submitting ? "Committing…" : "Confirm & commit"}
          </Button>
        </div>
      </div>
    </div>
  );
}
