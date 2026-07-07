/**
 * Run → Policies tab. Renders the OPA Policy Check step's structured result
 * (stamped into the step's summary_json by the executor): the gate mode/status
 * and the per-resource violations + warnings.
 */
import { useEffect, useState } from "react";

import { api } from "../api/client";
import { Badge } from "./ui";

type Violation = {
  policy: string;
  severity: "block" | "warn" | "info";
  level: "deny" | "warn";
  msg: string;
  resource: string | null;
};

type OpaSummary = {
  mode: string;
  status: string;
  violations: Violation[];
  warnings: Violation[];
  counts: { failures: number; warnings: number; blocking: number };
};

const SEVERITY_TONE: Record<string, "danger" | "warning" | "info"> = {
  block: "danger",
  warn: "warning",
  info: "info",
};

export const POLICY_STATUS_TONE: Record<string, "success" | "warning" | "danger" | "neutral"> = {
  passed: "success",
  warned: "warning",
  failed: "danger",
  not_run: "neutral",
};

export function PolicyStatusBadge({ status }: { status?: string | null }) {
  const s = status || "not_run";
  if (s === "not_run") return null;
  return <Badge tone={POLICY_STATUS_TONE[s] ?? "neutral"}>policy: {s}</Badge>;
}

function Rows({ items }: { items: Violation[] }) {
  return (
    <table className="w-full text-left text-sm">
      <thead className="text-xs uppercase text-slate-500">
        <tr><th className="py-1 pr-2">Policy</th><th className="pr-2">Severity</th><th className="pr-2">Resource</th><th>Message</th></tr>
      </thead>
      <tbody>
        {items.map((v, i) => (
          <tr key={i} className="border-t border-slate-100 dark:border-slate-800">
            <td className="py-1 pr-2 font-mono text-xs">{v.policy}</td>
            <td className="pr-2"><Badge tone={SEVERITY_TONE[v.severity] ?? "neutral"}>{v.severity}</Badge></td>
            <td className="pr-2 font-mono text-xs">{v.resource ?? "—"}</td>
            <td>{v.msg}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function PolicyResults({ runId }: { runId: string }) {
  const [summary, setSummary] = useState<OpaSummary | null>(null);
  const [stepStatus, setStepStatus] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api
      .get(`/v1/runs/${runId}/steps`)
      .then((r) => {
        if (cancelled) return;
        const step = (r.data ?? []).find((s: any) => s.name === "OPA Policy Check");
        setStepStatus(step?.status ?? null);
        if (step?.summary_json) {
          try { setSummary(JSON.parse(step.summary_json)); } catch { /* noop */ }
        }
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [runId]);

  if (loading) return <p className="text-sm italic text-slate-500">Loading…</p>;
  if (stepStatus === null) return <p className="text-sm text-slate-500">This run has no OPA Policy Check step.</p>;
  if (stepStatus === "skipped" || !summary)
    return <p className="text-sm text-slate-500">OPA policy gate was off for this run (no policies evaluated).</p>;

  const all = [...(summary.violations ?? []), ...(summary.warnings ?? [])];
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Badge tone={POLICY_STATUS_TONE[summary.status] ?? "neutral"}>{summary.status}</Badge>
        <span className="text-xs text-slate-500">
          mode={summary.mode} · {summary.counts.failures} failure(s), {summary.counts.warnings} warning(s),
          {" "}{summary.counts.blocking} blocking
        </span>
      </div>
      {all.length === 0 ? (
        <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-300">✓ No policy findings.</div>
      ) : (
        <Rows items={all} />
      )}
    </div>
  );
}
