import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import CopyButton from "./CopyButton";
import { cx } from "./ui";

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

function fmtDuration(sec: number | null): string {
  if (sec == null) return "";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const hhmmss = `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return ` · ${hhmmss}`;
}

/**
 * Concatenate every step's output into one continuous, terminal-style log —
 * each phase prefixed by a `=== <name> === [<status>]` header. Steps that have
 * neither output nor a terminal status are skipped so the stream stays clean.
 */
function buildCombined(steps: Step[]): string {
  const parts: string[] = [];
  for (const s of steps) {
    const hasOutput = !!(s.output && s.output.trim());
    // Keep headers for steps that ran (or are running) even if output is empty;
    // drop never-reached pending steps entirely.
    if (!hasOutput && s.status === "pending") continue;
    if (!hasOutput && s.status === "skipped") continue;
    const header = `=== ${s.name} === [${s.status}${fmtDuration(s.duration_seconds)}]`;
    const body = hasOutput ? s.output! : "(no output captured)";
    parts.push(`${header}\n${body}`);
  }
  return parts.join("\n\n");
}

type Props = {
  runId: string;
  /** Short label for the dialog title, e.g. the first 8 chars of the run id. */
  label?: string;
  onClose: () => void;
};

/**
 * Full-run log viewer: a single modal that streams ALL of a run's step output
 * into one scrolling view. Reuses the per-step `/steps` endpoint and mirrors the
 * adaptive-poll + sticky-to-bottom behavior of the timeline's step detail.
 */
export default function RunLogsModal({ runId, label, onClose }: Props) {
  const [steps, setSteps] = useState<Step[]>([]);
  const [loading, setLoading] = useState(true);
  const preRef = useRef<HTMLPreElement | null>(null);
  const stickRef = useRef<boolean>(true); // stick to bottom unless user scrolls up

  async function load() {
    try {
      const r = await api.get(`/v1/runs/${runId}/steps`);
      setSteps(r.data);
    } finally {
      setLoading(false);
    }
  }

  // Poll while anything is still in flight; stop once every step is terminal.
  const hasRunning = steps.some((s) => s.status === "running" || s.status === "pending");
  const pollInterval = hasRunning ? 1500 : 0;

  useEffect(() => {
    void load();
    if (pollInterval === 0) return;
    const t = window.setInterval(() => void load(), pollInterval);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, pollInterval]);

  // ESC closes.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const combined = useMemo(() => buildCombined(steps), [steps]);

  function onScroll() {
    const el = preRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.clientHeight - el.scrollTop;
    stickRef.current = distanceFromBottom < 24;
  }

  // Follow the tail while streaming, unless the user has scrolled up.
  useEffect(() => {
    const el = preRef.current;
    if (!el) return;
    if (stickRef.current) el.scrollTop = el.scrollHeight;
  }, [combined]);

  function download() {
    const blob = new Blob([combined || ""], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `run-${label ?? runId}-logs.txt`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  return (
    <div
      role="dialog"
      aria-label={`Run ${label ?? runId} — all logs`}
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 pt-10 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="flex h-[calc(100vh-80px)] w-full max-w-6xl flex-col rounded-lg border border-slate-200 bg-white shadow-xl dark:border-slate-700/80 dark:bg-slate-900"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3 dark:border-slate-800">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-slate-100">
            All logs{label ? ` · Run ${label}` : ""}
            {hasRunning && (
              <span className="inline-flex items-center gap-1 rounded-full bg-sky-100 px-2 py-0.5 text-[10px] font-medium text-sky-700 dark:bg-sky-900/40 dark:text-sky-300">
                <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
                streaming
              </span>
            )}
          </h3>
          <div className="flex items-center gap-2">
            <CopyButton getText={() => combined} title="Copy all logs to clipboard" />
            <button
              type="button"
              onClick={download}
              className="rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
              title="Download all logs as a .txt file"
            >
              Download
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded-md px-2 py-1 text-xs text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
              title="Close (Esc)"
            >
              Close
            </button>
          </div>
        </div>
        <pre
          ref={preRef}
          onScroll={onScroll}
          className={cx(
            "m-5 flex-1 overflow-auto whitespace-pre-wrap rounded-md border p-3 font-mono text-[12px] leading-relaxed",
            "border-slate-200 bg-white text-slate-800 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-200",
          )}
        >
          {loading && steps.length === 0
            ? "Loading…"
            : combined || "No output captured yet — the run hasn't produced any logs."}
        </pre>
      </div>
    </div>
  );
}
