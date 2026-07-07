import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import CopyButton from "./CopyButton";
import PlanSummary from "./PlanSummary";
import { RunningDuck, Skeleton, cx } from "./ui";

type StepStatus = "pending" | "running" | "success" | "failed" | "skipped";

type Step = {
  id: string;
  position: number;
  name: string;
  status: StepStatus;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  output: string | null;
  summary_json: string | null;
};

const STATUS_PILL: Record<StepStatus, string> = {
  pending: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",
  running: "bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300",
  success: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
  failed: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
  skipped: "bg-slate-100 text-slate-500 dark:bg-slate-800/60 dark:text-slate-500",
};

function StatusPill({ status }: { status: StepStatus }) {
  const label = status === "success" ? "Success" :
    status === "running" ? "Running" :
    status === "failed" ? "Failed" :
    status === "skipped" ? "Skipped" : "Pending";
  return (
    <span
      className={cx(
        "inline-flex min-w-[72px] items-center justify-center rounded-md px-2.5 py-1 text-xs font-medium",
        STATUS_PILL[status],
      )}
    >
      {status === "running" && <RunningDuck size={15} className="mr-1.5 -ml-0.5" />}
      {label}
    </span>
  );
}

function formatDuration(sec: number | null): string {
  if (sec == null) return "—";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function PlanDiff({ summary }: { summary: string | null }) {
  if (!summary) return null;
  let parsed: any;
  try {
    parsed = JSON.parse(summary);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed == null) return null;
  if (!("add" in parsed) && !("change" in parsed) && !("destroy" in parsed)) return null;
  const add = parsed.add ?? 0;
  const change = parsed.change ?? 0;
  const destroy = parsed.destroy ?? 0;
  return (
    <span className="inline-flex items-center gap-2 text-xs font-mono">
      <span className="text-emerald-600 dark:text-emerald-400">+{add}</span>
      <span className="text-amber-600 dark:text-amber-400">~{change}</span>
      <span className="text-red-600 dark:text-red-400">−{destroy}</span>
    </span>
  );
}

// ID used for the synthetic, UI-only "Plan Summary" row that gets inserted
// after the real "Terraform Plan" step. Keeping it as a constant so the open
// state and the iteration logic both reference the same thing.
const PLAN_SUMMARY_SYNTHETIC_ID = "__plan_summary__";

function isTerraformPlanStep(name: string): boolean {
  // Tolerant match — the real step is "Terraform Plan" today, but the casing
  // could drift. We just want any step whose name is the plan phase.
  return /terraform\s*plan/i.test(name);
}

function isTerraformApplyStep(name: string): boolean {
  return /terraform\s*apply/i.test(name);
}

// Steps that benefit from a roomier viewer. Plan and apply produce the
// largest outputs (resource lists, diff blocks, real-time apply progress)
// and are the most useful to inspect in a bigger window.
function supportsLargerView(name: string): boolean {
  return isTerraformPlanStep(name) || isTerraformApplyStep(name);
}

export type ApprovalActions = {
  // Show the buttons (gated on operator role + awaiting_approval at the call site).
  visible: boolean;
  busy: string | null;
  onApprove: () => void;
  onReject: () => void;
};

type RunStepsProps = {
  runId: string;
  approval?: ApprovalActions;
};

export default function RunSteps({ runId, approval }: RunStepsProps) {
  const [steps, setSteps] = useState<Step[]>([]);
  const [loading, setLoading] = useState(true);
  const [openId, setOpenId] = useState<string | null>(null);

  async function load() {
    try {
      const r = await api.get(`/v1/runs/${runId}/steps`);
      setSteps(r.data);
    } finally {
      setLoading(false);
    }
  }

  // Adaptive poll: 1s when an in-progress step is expanded (so streamed
  // `terraform plan/apply` output animates in near real-time), otherwise 3s.
  // Stops polling entirely once every step is in a terminal state.
  const hasRunning = steps.some((s) => s.status === "running" || s.status === "pending");
  const openIsRunning = !!steps.find((s) => s.id === openId && s.status === "running");
  const pollInterval = openIsRunning ? 1000 : hasRunning ? 3000 : 0;

  useEffect(() => {
    load();
    if (pollInterval === 0) return;
    const t = window.setInterval(() => {
      void load();
    }, pollInterval);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, pollInterval]);

  if (loading && steps.length === 0) {
    return (
      <div className="space-y-2">
        {[0, 1, 2, 3, 4].map((i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }

  if (steps.length === 0) {
    return (
      <p className="text-sm italic text-slate-500">No timeline yet — the run hasn&apos;t started.</p>
    );
  }

  // Build the rendered timeline: the real backend steps plus a synthetic
  // "Plan Summary" row injected right after the Terraform Plan step. We don't
  // persist this step to the DB — it's a pure UI affordance backed by the
  // plan_json already attached to the run.
  type Row =
    | { kind: "real"; step: Step }
    | { kind: "synthetic-plan-summary"; planStep: Step };
  const rows: Row[] = [];
  for (const s of steps) {
    rows.push({ kind: "real", step: s });
    if (isTerraformPlanStep(s.name)) {
      rows.push({ kind: "synthetic-plan-summary", planStep: s });
    }
  }

  return (
    <div className="overflow-hidden rounded-md border border-slate-200 dark:border-slate-800/80">
      {rows.map((row, i) => {
        if (row.kind === "real") {
          const s = row.step;
          const isOpen = openId === s.id;
          return (
            <div
              key={s.id}
              className={cx(
                i > 0 ? "border-t border-slate-100 dark:border-slate-800/60" : "",
                s.status === "running"
                  ? "bg-sky-50/50 dark:bg-sky-950/20"
                  : "bg-white dark:bg-slate-900/40",
              )}
            >
              <button
                type="button"
                onClick={() => setOpenId(isOpen ? null : s.id)}
                className="flex w-full items-center gap-3 px-4 py-3 text-left text-sm transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/40"
              >
                <span className="text-xs font-mono text-slate-400 w-5">{s.position + 1}</span>
                <span className="flex-1 truncate font-medium text-slate-900 dark:text-slate-100">
                  {s.name}
                </span>
                <PlanDiff summary={s.summary_json} />
                <StatusPill status={s.status} />
                <span className="font-mono text-xs text-slate-500 w-16 text-right">
                  {formatDuration(s.duration_seconds)}
                </span>
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="currentColor"
                  className={cx("transition-transform text-slate-400", isOpen ? "rotate-90" : "rotate-0")}
                  aria-hidden
                >
                  <path d="M9 6l6 6-6 6V6Z" />
                </svg>
              </button>
              {isOpen && (
                <StepDetail
                  step={s}
                  approval={isTerraformPlanStep(s.name) ? approval : undefined}
                />
              )}
            </div>
          );
        }

        // Synthetic Plan Summary row — only meaningful once the plan step has
        // finished. While the plan is still running we render the row as
        // pending so the position in the timeline is visible.
        const plan = row.planStep;
        const planFinished = plan.status === "success";
        const status: StepStatus = planFinished
          ? "success"
          : plan.status === "failed"
            ? "skipped"
            : "pending";
        const isOpen = openId === PLAN_SUMMARY_SYNTHETIC_ID;
        return (
          <div
            key={PLAN_SUMMARY_SYNTHETIC_ID}
            className={cx(
              "border-t border-slate-100 dark:border-slate-800/60",
              "bg-white dark:bg-slate-900/40",
            )}
          >
            <button
              type="button"
              onClick={() => planFinished && setOpenId(isOpen ? null : PLAN_SUMMARY_SYNTHETIC_ID)}
              disabled={!planFinished}
              className={cx(
                "flex w-full items-center gap-3 px-4 py-3 text-left text-sm transition-colors",
                planFinished
                  ? "hover:bg-slate-50 dark:hover:bg-slate-800/40"
                  : "cursor-default opacity-70",
              )}
            >
              <span className="text-xs font-mono text-slate-400 w-5" aria-hidden>
                Σ
              </span>
              <span className="flex-1 truncate font-medium text-slate-900 dark:text-slate-100">
                Plan Summary
              </span>
              <PlanDiff summary={plan.summary_json} />
              <StatusPill status={status} />
              <span className="font-mono text-xs text-slate-500 w-16 text-right">—</span>
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="currentColor"
                className={cx("transition-transform text-slate-400", isOpen ? "rotate-90" : "rotate-0")}
                aria-hidden
              >
                <path d="M9 6l6 6-6 6V6Z" />
              </svg>
            </button>
            {isOpen && (
              <div className="border-t border-slate-100 bg-slate-50/80 px-4 py-3 dark:border-slate-800/60 dark:bg-slate-950/40">
                <PlanSummary runId={runId} />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ─── per-step detail panel with auto-scroll ────────────────────────────────

function StepDetail({ step, approval }: { step: Step; approval?: ApprovalActions }) {
  const preRef = useRef<HTMLPreElement | null>(null);
  const stickRef = useRef<boolean>(true); // stick to bottom unless user scrolls up
  const [fullscreen, setFullscreen] = useState(false);

  // Detect whether the user has scrolled up — if so, stop auto-following.
  function onScroll() {
    const el = preRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.clientHeight - el.scrollTop;
    stickRef.current = distanceFromBottom < 24;
  }

  // After every output update, scroll to bottom if we were already there.
  useEffect(() => {
    const el = preRef.current;
    if (!el) return;
    if (stickRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [step.output]);

  // ESC closes the fullscreen viewer.
  useEffect(() => {
    if (!fullscreen) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setFullscreen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [fullscreen]);

  const isStreaming = step.status === "running";
  const isFailed = step.status === "failed";
  const canEnlarge = supportsLargerView(step.name) && !!step.output;

  return (
    <div className="border-t border-slate-100 bg-slate-50/80 px-4 py-3 dark:border-slate-800/60 dark:bg-slate-950/40">
      {step.output ? (
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="text-[10px] uppercase tracking-wider text-slate-500">
              {isStreaming ? "live output" : "output"}
            </span>
            <div className="flex items-center gap-2">
              {isStreaming && (
                <span className="inline-flex items-center gap-1 rounded-full bg-sky-100 px-2 py-0.5 text-[10px] font-medium text-sky-700 dark:bg-sky-900/40 dark:text-sky-300">
                  <RunningDuck size={13} />
                  streaming
                </span>
              )}
              {step.output && (
                <CopyButton
                  getText={() => step.output ?? ""}
                  title="Copy this step's output to clipboard"
                />
              )}
              {canEnlarge && (
                <button
                  type="button"
                  onClick={() => setFullscreen(true)}
                  className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
                  title="Open this output in a larger window"
                >
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
                    <path d="M4 4h7v2H6v5H4V4Zm9 0h7v7h-2V6h-5V4ZM4 13h2v5h5v2H4v-7Zm14 0h2v7h-7v-2h5v-5Z" />
                  </svg>
                  Open larger
                </button>
              )}
            </div>
          </div>
          <pre
            ref={preRef}
            onScroll={onScroll}
            className={cx(
              "max-h-72 overflow-auto whitespace-pre-wrap rounded-md border p-2.5 font-mono text-[11px] leading-relaxed",
              isFailed
                ? "border-red-200 bg-red-50 text-red-800 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300"
                : "border-slate-200 bg-white text-slate-700 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300",
            )}
          >
            {step.output}
          </pre>
        </div>
      ) : isStreaming ? (
        <p className="flex items-center gap-2 text-xs italic text-slate-500">
          <RunningDuck size={18} />
          working… waiting for first line of output
        </p>
      ) : (
        <p className="text-xs italic text-slate-500">no output captured for this step yet</p>
      )}
      <div className="mt-2 grid grid-cols-2 gap-2 text-[11px] text-slate-500">
        <span>started: {step.started_at ? new Date(step.started_at).toLocaleString() : "—"}</span>
        <span>completed: {step.completed_at ? new Date(step.completed_at).toLocaleString() : "—"}</span>
      </div>

      {approval?.visible && (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-200">
          <span className="font-medium">Plan ready — needs approval before apply.</span>
          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={approval.onApprove}
              disabled={!!approval.busy}
              className="inline-flex items-center gap-1 rounded-md bg-emerald-500 px-3 py-1 text-xs font-medium text-white shadow hover:bg-emerald-600 disabled:cursor-not-allowed disabled:opacity-50"
              title="Approve and run terraform apply"
            >
              {approval.busy === "approve" ? "…" : "✓ Approve"}
            </button>
            <button
              type="button"
              onClick={approval.onReject}
              disabled={!!approval.busy}
              className="inline-flex items-center gap-1 rounded-md border border-red-300 bg-white px-3 py-1 text-xs font-medium text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-red-900/40 dark:bg-slate-900 dark:text-red-300 dark:hover:bg-red-950/40"
              title="Reject this plan; the run will be cancelled"
            >
              {approval.busy === "reject" ? "…" : "✕ Decline"}
            </button>
          </div>
        </div>
      )}

      {fullscreen && (
        <div
          role="dialog"
          aria-label={`${step.name} — full output`}
          className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 pt-10 backdrop-blur-sm"
          onClick={() => setFullscreen(false)}
        >
          <div
            className="flex w-full max-w-6xl flex-col rounded-lg border border-slate-200 bg-white shadow-xl dark:border-slate-700/80 dark:bg-slate-900"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3 dark:border-slate-800">
              <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                {step.name}
                {isStreaming && (
                  <span className="ml-2 inline-flex items-center gap-1 rounded-full bg-sky-100 px-2 py-0.5 text-[10px] font-medium text-sky-700 dark:bg-sky-900/40 dark:text-sky-300">
                    <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
                    streaming
                  </span>
                )}
              </h3>
              <div className="flex items-center gap-2">
                <CopyButton getText={() => step.output ?? ""} />
                <button
                  type="button"
                  onClick={() => setFullscreen(false)}
                  className="rounded-md px-2 py-1 text-xs text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
                  title="Close (Esc)"
                >
                  Close
                </button>
              </div>
            </div>
            <pre
              className={cx(
                "m-5 overflow-auto whitespace-pre-wrap rounded-md border p-3 font-mono text-[12px] leading-relaxed",
                "max-h-[calc(100vh-160px)]",
                isFailed
                  ? "border-red-200 bg-red-50 text-red-800 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300"
                  : "border-slate-200 bg-white text-slate-800 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-200",
              )}
              ref={(el) => {
                // Keep auto-follow behavior in the fullscreen view too while
                // the step is still streaming.
                if (el && isStreaming) el.scrollTop = el.scrollHeight;
              }}
            >
              {step.output}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}
