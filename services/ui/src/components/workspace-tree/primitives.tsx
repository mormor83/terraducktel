// Low-level row primitives shared across the tree: the generic TreeRow, the
// bulk webhook-enable button (used by account/region/folder rows), and the
// branch + status chip (used by the workspace leaf).

import { ReactNode, useState } from "react";
import { api } from "../../api/client";
import { useCurrentUser, hasMinRole } from "../../hooks/useAuth";
import { ConfirmDialog, cx } from "../ui";
import { ChevronIcon } from "./icons";
import type { Run, Workspace } from "./types";

export function TreeRow({
  depth,
  open,
  onToggle,
  icon,
  label,
  meta,
  right,
  hoverable = true,
  className,
}: {
  depth: number;
  open?: boolean;
  onToggle?: () => void;
  icon: ReactNode;
  label: ReactNode;
  meta?: ReactNode;
  right?: ReactNode;
  hoverable?: boolean;
  className?: string;
}) {
  // depth 0 = account, 1 = region, 2+ = nested folders + workspace leaf.
  // The legacy values 16/36/56 are preserved for the first three depths; deeper
  // levels (created when a repo path has intermediate folders) step by 20px.
  const padding = 16 + depth * 20;
  const Comp: any = onToggle ? "button" : "div";
  return (
    <Comp
      type={onToggle ? "button" : undefined}
      onClick={onToggle}
      style={{ paddingLeft: padding }}
      className={cx(
        "flex w-full items-center gap-3 py-2.5 pr-4 text-left transition-colors",
        hoverable && "hover:bg-slate-50 dark:hover:bg-slate-800/40",
        onToggle ? "cursor-pointer focus:outline-none focus:bg-slate-100 dark:focus:bg-slate-800/60" : "",
        className,
      )}
    >
      {onToggle ? <ChevronIcon open={!!open} /> : <span className="w-[14px]" />}
      {icon}
      <span className="min-w-0 flex-1 text-sm">
        <span className="truncate text-slate-800 dark:text-slate-100">{label}</span>
        {meta && <span className="ml-2 text-xs text-slate-500 dark:text-slate-500">{meta}</span>}
      </span>
      {right && <span className="ml-auto flex shrink-0 items-center gap-2">{right}</span>}
    </Comp>
  );
}

// ─── Bulk webhook-enable button (used by Account / Region / Folder rows) ───

/**
 * Filter a workspace list down to the ones we can flip auto-trigger on:
 * - must be git-synced (a `local://` workspace has no upstream to push to)
 * - must not be orphaned (the path no longer exists in the repo, so a push
 *   could never match anyway)
 * - must not already be enabled (skip silently rather than flip-to-flip)
 */
export function eligibleForWebhookBulk(workspaces: Workspace[]): Workspace[] {
  return workspaces.filter((w) => {
    const repo = (w.repo_url ?? "").trim();
    if (!repo || repo.startsWith("local://")) return false;
    if (w.path_status === "orphaned") return false;
    if (w.webhook_enabled) return false;
    return true;
  });
}

export function BulkWebhookButton({
  collect,
  scopeLabel,
  onChanged,
}: {
  collect: () => Workspace[];
  scopeLabel: string;
  onChanged: () => void;
}) {
  const user = useCurrentUser();
  const [busy, setBusy] = useState(false);
  // In-app confirm + result (no native confirm()/alert()).
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  if (!hasMinRole(user, "admin")) return null;
  const candidates = eligibleForWebhookBulk(collect());
  // Nothing left to enable and nothing to report → render nothing.
  if (candidates.length === 0 && !confirmOpen && !result) return null;

  async function runBulk() {
    setConfirmOpen(false);
    setBusy(true);
    let ok = 0;
    let fail = 0;
    for (const ws of candidates) {
      try {
        await api.put(`/v1/workspaces/${ws.id}`, { webhook_enabled: true });
        ok += 1;
      } catch {
        fail += 1;
      }
    }
    setBusy(false);
    setResult(
      `Enabled auto-trigger on ${ok} workspace${ok === 1 ? "" : "s"}${
        fail ? ` · ${fail} failed` : ""
      }.`,
    );
    onChanged();
  }

  return (
    <>
      {candidates.length > 0 && (
        <button
          type="button"
          onClick={(e) => {
            // The parent TreeRow's onToggle expands the group on click — don't
            // let this button bubble up and flip the chevron.
            e.stopPropagation();
            setConfirmOpen(true);
          }}
          disabled={busy}
          title={`Enable auto-trigger on push for ${candidates.length} workspace(s) below ${scopeLabel}`}
          className="rounded border border-slate-300 bg-white px-2 py-0.5 text-[11px] font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
        >
          {busy ? "…" : `⚡ enable webhook on ${candidates.length}`}
        </button>
      )}
      <ConfirmDialog
        open={confirmOpen}
        title="Enable auto-trigger on push"
        message={
          <>
            Enable auto-trigger on push for {candidates.length} workspace
            {candidates.length === 1 ? "" : "s"} under {scopeLabel}? Only git-synced, non-orphaned
            workspaces that aren't already enabled are included. You can still disable each one
            individually afterwards.
          </>
        }
        confirmLabel="Enable"
        tone="warning"
        busy={busy}
        onConfirm={runBulk}
        onCancel={() => setConfirmOpen(false)}
      />
      <ConfirmDialog
        open={result !== null}
        title="Auto-trigger updated"
        message={result ?? ""}
        confirmLabel="OK"
        cancelLabel="Close"
        tone="primary"
        onConfirm={() => setResult(null)}
        onCancel={() => setResult(null)}
      />
    </>
  );
}

// ─── Inline "link" editor ──────────────────────────────────────────────────
//
// A label + a select-to-rebind editor used in the workspace leaf detail for
// the two near-identical credential links: `state aws account` and
// `azure subscription`. Collapsed it shows the current value + a "change"
// link; expanded it shows a <select> + Save/cancel. `onSave` returns whether
// the save succeeded — on failure the editor stays open (the parent surfaces
// the error), matching the previous per-field behavior.

export function InlineLinkEditor({
  label,
  labelTitle,
  changeTitle,
  emptyLabel,
  options,
  current,
  display,
  monoSelect = false,
  canEdit,
  busy,
  onSave,
  bare = false,
}: {
  label: string;
  labelTitle: string;
  changeTitle: string;
  emptyLabel: string;
  options: { value: string; label: string }[];
  // The currently-stored value ("" = unset). Drives the dirty check + cancel.
  current: string;
  // What to render when collapsed (already-formatted value text).
  display: ReactNode;
  monoSelect?: boolean;
  canEdit: boolean;
  busy: boolean;
  onSave: (value: string) => Promise<boolean>;
  // When true, render just the value/editor (no label, no col-span wrapper) so
  // it drops into a table value cell — the caller supplies the label column.
  bare?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(current);
  const body =
    editing ? (
        <span className="inline-flex items-center gap-1.5">
          <select
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className={cx(
              "rounded border border-slate-300 bg-white px-1.5 py-0.5 text-[11px] dark:border-slate-700 dark:bg-slate-950",
              monoSelect && "font-mono",
            )}
          >
            <option value="">{emptyLabel}</option>
            {options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={async () => {
              if (await onSave(draft)) setEditing(false);
            }}
            disabled={busy || draft === current}
            className="rounded bg-brand-500 px-2 py-0.5 text-[11px] font-medium text-white hover:bg-brand-400 disabled:opacity-50"
          >
            {busy ? "…" : "Save"}
          </button>
          <button
            type="button"
            onClick={() => {
              setEditing(false);
              setDraft(current);
            }}
            className="text-[11px] text-slate-500 hover:underline"
          >
            cancel
          </button>
        </span>
      ) : (
        <>
          <span className="font-mono text-[11px]">{display}</span>
          {canEdit && (
            <button
              type="button"
              onClick={() => {
                setDraft(current);
                setEditing(true);
              }}
              className="ml-2 text-[11px] text-brand-500 hover:underline"
              title={changeTitle}
            >
              change
            </button>
          )}
        </>
      );
  if (bare) return <>{body}</>;
  return (
    <div className="sm:col-span-2">
      <span className="text-slate-400" title={labelTitle}>
        {label}
      </span>{" "}
      {body}
    </div>
  );
}

// ─── Branch + status chip ────────────────────────────────────────────────────
//
// Replaces the old click-to-change Branch button. The chip combines the
// current branch with a color that reflects the most recent run's state:
//   - grey  : idle / never run
//   - amber : running
//   - green : last run succeeded
//   - red   : last run failed
//   - blue  : awaiting approval
//   - orange: drift detected (handled separately by DriftBadge)
//
// To change the branch the operator opens the Run modal — picking a branch
// there persists it to the workspace whether or not a run is started.

export function BranchStatusChip({
  branch,
  run,
  webhookEnabled,
}: {
  branch: string;
  run?: Run;
  webhookEnabled: boolean;
}) {
  const tone = statusTone(run?.status);
  const TONE: Record<string, string> = {
    idle:    "bg-slate-100 text-slate-700 ring-slate-300/80 dark:bg-slate-800/80 dark:text-slate-300 dark:ring-slate-700/50",
    running: "bg-amber-100 text-amber-800 ring-amber-300/70 dark:bg-amber-900/30 dark:text-amber-200 dark:ring-amber-700/50 animate-pulse",
    passed:  "bg-emerald-100 text-emerald-800 ring-emerald-300/70 dark:bg-emerald-900/30 dark:text-emerald-200 dark:ring-emerald-700/50",
    failed:  "bg-red-100 text-red-800 ring-red-300/70 dark:bg-red-900/30 dark:text-red-200 dark:ring-red-700/50",
    review:  "bg-blue-100 text-blue-800 ring-blue-300/70 dark:bg-blue-900/30 dark:text-blue-200 dark:ring-blue-700/50",
  };
  // Keep the label short — the branch is already shown in the chip and the
  // tooltip, so don't repeat it here (a long branch in the label was pushing
  // the row's action buttons off-screen).
  const label = run?.status ? humanStatus(run.status) : "no runs";
  return (
    <span
      title={`Branch: ${branch}${webhookEnabled ? " · auto-trigger on push" : ""} — ${label}`}
      className={cx(
        "inline-flex max-w-[16rem] items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset",
        TONE[tone],
      )}
    >
      <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" aria-hidden className="shrink-0">
        <path d="M6 3a3 3 0 0 1 1 5.83V15a3 3 0 0 0 3 3h1V14a3 3 0 1 1 2 0v4h1a5 5 0 0 0 5-5V8.83A3 3 0 1 0 17 3a3 3 0 0 0 0 5.83V13a3 3 0 0 1-3 3h-1v-2a3 3 0 0 0-2-2.83V8.83A3 3 0 0 0 6 3Z" />
      </svg>
      {/* Cap the branch so a long name (e.g. fix/langfuse-rds-bump-v1.12.1)
          can't push the chip wide enough to overlap the leaf label. Full
          branch stays in the chip's title tooltip. */}
      <span className="min-w-0 max-w-[10rem] truncate font-mono">{branch}</span>
      {webhookEnabled && <span className="shrink-0 text-sky-600 dark:text-sky-400" title="auto-trigger on push enabled">⚡</span>}
      <span className="shrink-0 text-[10px] uppercase opacity-70">· {label}</span>
    </span>
  );
}

function statusTone(s?: string): "idle" | "running" | "passed" | "failed" | "review" {
  if (!s) return "idle";
  switch (s.toLowerCase()) {
    case "pending":
    case "running":
    case "applying":
      return "running";
    case "succeeded":
    case "applied":
      return "passed";
    case "failed":
    case "errored":
    case "cancelled":
      return "failed";
    case "awaiting_approval":
    case "needs_approval":
      return "review";
    default:
      return "idle";
  }
}

function humanStatus(s: string): string {
  return s.replace(/_/g, " ").toLowerCase();
}
