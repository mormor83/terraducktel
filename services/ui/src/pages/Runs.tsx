import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import {
  Badge,
  Button,
  Card,
  CardBody,
  ConfirmDialog,
  EmptyState,
  Input,
  RunStatusBadge,
  SectionHeader,
  Skeleton,
  cx,
} from "../components/ui";
import { useCurrentUser, hasMinRole } from "../hooks/useAuth";
import RunSteps from "../components/RunSteps";
import RunLogsModal from "../components/RunLogsModal";

type Run = {
  id: string;
  workspace_id: string;
  status: string;
  command: string;
  branch?: string | null;
  plan_output?: string | null;
  error_output?: string | null;
  triggered_by?: string | null;
  reviewer_id?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
};

type Workspace = {
  id: string;
  name: string;
  environment: string;
  region: string;
  aws_account_id: string;
  tf_working_dir?: string;
};

type UserLite = {
  id: string;
  email: string;
};

type DetailTab = "timeline" | "plan" | null;
type GroupMode = "time" | "workspace";

const REFRESH_INTERVAL_MS = 8_000;

function relTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const sec = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day}d ago`;
  return d.toLocaleDateString();
}

function durationOf(run: Run): string | null {
  if (!run.started_at) return null;
  const end = run.completed_at ? new Date(run.completed_at).getTime() : Date.now();
  const sec = Math.max(0, Math.floor((end - new Date(run.started_at).getTime()) / 1000));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m === 0) return `${s}s`;
  return `${m}m ${s}s`;
}

const ENV_TONE: Record<string, "success" | "warning" | "info" | "neutral" | "amber" | "violet"> = {
  prod: "warning",
  preprod: "violet",
  staging: "info",
  dev: "success",
  shared: "neutral",
};

export default function Runs() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [workspaces, setWorkspaces] = useState<Record<string, Workspace>>({});
  const [users, setUsers] = useState<Record<string, UserLite>>({});
  const [openRunId, setOpenRunId] = useState<string | null>(null);
  const [openTab, setOpenTab] = useState<DetailTab>(null);
  const [planCache, setPlanCache] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [busyCancel, setBusyCancel] = useState<string | null>(null);
  const [approveRunId, setApproveRunId] = useState<string | null>(null);
  const [rejectRunId, setRejectRunId] = useState<string | null>(null);
  const [cancelRunId, setCancelRunId] = useState<string | null>(null);
  // Action failures surfaced inline (no native alert()).
  const [actionErr, setActionErr] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  // Honor ?status=… from the URL (e.g. Dashboard "Awaiting approval" tile,
  // /approvals redirect) so deep-links land pre-filtered.
  const [statusFilter, setStatusFilter] = useState<string>(
    () => new URLSearchParams(window.location.search).get("status") || "all",
  );
  const [grouping, setGrouping] = useState<GroupMode>("time");
  const [busyRerun, setBusyRerun] = useState<string | null>(null);
  // Run whose full combined-logs modal is open (null = closed). Independent of
  // the inline Timeline/Plan expansion so logs are reachable without drilling in.
  const [logsRunId, setLogsRunId] = useState<string | null>(null);
  const user = useCurrentUser();
  const nav = useNavigate();

  async function load() {
    try {
      const [r, w, u] = await Promise.all([
        api.get("/v1/runs"),
        api.get("/v1/workspaces"),
        api.get("/v1/users").catch(() => ({ data: [] })), // viewer 403 → empty
      ]);
      setRuns(r.data);
      const wsMap: Record<string, Workspace> = {};
      for (const x of w.data) wsMap[x.id] = x;
      setWorkspaces(wsMap);
      const uMap: Record<string, UserLite> = {};
      for (const x of u.data) uMap[x.id] = x;
      setUsers(uMap);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    const t = window.setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, REFRESH_INTERVAL_MS);
    return () => window.clearInterval(t);
  }, []);

  async function openTimeline(runId: string) {
    if (openRunId === runId && openTab === "timeline") {
      setOpenRunId(null);
      setOpenTab(null);
      return;
    }
    setOpenRunId(runId);
    setOpenTab("timeline");
  }

  async function openPlan(runId: string) {
    if (openRunId === runId && openTab === "plan") {
      setOpenRunId(null);
      setOpenTab(null);
      return;
    }
    if (!planCache[runId]) {
      const r = await api.get(`/v1/runs/${runId}/plan`);
      setPlanCache((p) => ({ ...p, [runId]: r.data.plan_output || "(no plan output yet)" }));
    }
    setOpenRunId(runId);
    setOpenTab("plan");
  }

  async function cancelRun(runId: string) {
    setCancelRunId(null);
    setActionErr(null);
    setBusyCancel(runId);
    try {
      await api.post(`/v1/runs/${runId}/cancel`);
      await load();
    } catch (err: any) {
      setActionErr(err?.response?.data?.detail ?? "Cancel failed");
    } finally {
      setBusyCancel(null);
    }
  }

  async function approveRun(runId: string) {
    setApproveRunId(null);
    setActionErr(null);
    setBusyCancel(runId);
    try {
      await api.post(`/v1/runs/${runId}/approve`, { comment: "Approved from run detail" });
      await load();
    } catch (err: any) {
      setActionErr(err?.response?.data?.detail ?? "Approve failed");
    } finally {
      setBusyCancel(null);
    }
  }

  async function rejectRun(runId: string) {
    setRejectRunId(null);
    setActionErr(null);
    setBusyCancel(runId);
    try {
      await api.post(`/v1/runs/${runId}/reject`, { comment: "Rejected from run detail" });
      await load();
    } catch (err: any) {
      setActionErr(err?.response?.data?.detail ?? "Reject failed");
    } finally {
      setBusyCancel(null);
    }
  }

  // Re-run a failed/cancelled run without drilling into its detail page.
  // Same semantics as RunDetail.rerun: the trigger endpoint reads the
  // workspace's CURRENT branch server-side, so we just POST the command.
  async function rerun(run: Run) {
    setBusyRerun(run.id);
    try {
      const r = await api.post(`/v1/workspaces/${run.workspace_id}/runs`, {
        command: run.command,
      });
      nav(`/runs/${r.data.id}`);
    } catch (err: any) {
      setActionErr(err?.response?.data?.detail ?? "Re-run failed");
      setBusyRerun(null);
    }
  }

  // `planned` is terminal for a plan-only run (executor already exited) — not
  // cancellable. Apply runs pause at `awaiting_approval`, not `planned`.
  const cancellable = new Set([
    "pending",
    "running",
    "planning",
    "awaiting_approval",
  ]);

  const rerunnable = new Set(["failed", "cancelled"]);

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return runs
      .filter((r) => statusFilter === "all" || r.status === statusFilter)
      .filter((r) => {
        if (!q) return true;
        const ws = workspaces[r.workspace_id];
        const u = r.triggered_by ? users[r.triggered_by] : undefined;
        const haystack = [
          r.id,
          r.command,
          r.status,
          ws?.name ?? "",
          ws?.aws_account_id ?? "",
          ws?.region ?? "",
          ws?.environment ?? "",
          u?.email ?? "",
        ]
          .join(" ")
          .toLowerCase();
        return haystack.includes(q);
      })
      .slice()
      .sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
  }, [runs, workspaces, users, filter, statusFilter]);

  const statusCounts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const r of runs) c[r.status] = (c[r.status] ?? 0) + 1;
    return c;
  }, [runs]);

  // ─── grouping ──────────────────────────────────────────────────────────

  type Group = { key: string; label: string; runs: Run[] };

  const groups: Group[] = useMemo(() => {
    if (grouping === "workspace") {
      const buckets = new Map<string, Group>();
      for (const r of filtered) {
        const ws = workspaces[r.workspace_id];
        const key = r.workspace_id;
        const label = ws?.name ?? `(deleted workspace ${r.workspace_id.slice(0, 8)})`;
        if (!buckets.has(key)) buckets.set(key, { key, label, runs: [] });
        buckets.get(key)!.runs.push(r);
      }
      return [...buckets.values()].sort((a, b) =>
        (b.runs[0].created_at ?? "").localeCompare(a.runs[0].created_at ?? ""),
      );
    }
    // time grouping: today / yesterday / earlier this week / older
    const now = new Date();
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const startOfYesterday = startOfToday - 24 * 3600 * 1000;
    const startOfWeek = startOfToday - 7 * 24 * 3600 * 1000;

    const buckets: Group[] = [
      { key: "today", label: "Today", runs: [] },
      { key: "yesterday", label: "Yesterday", runs: [] },
      { key: "week", label: "Earlier this week", runs: [] },
      { key: "older", label: "Older", runs: [] },
    ];
    for (const r of filtered) {
      const t = r.created_at ? new Date(r.created_at).getTime() : 0;
      if (t >= startOfToday) buckets[0].runs.push(r);
      else if (t >= startOfYesterday) buckets[1].runs.push(r);
      else if (t >= startOfWeek) buckets[2].runs.push(r);
      else buckets[3].runs.push(r);
    }
    return buckets.filter((b) => b.runs.length > 0);
  }, [filtered, workspaces, grouping]);

  return (
    <div>
      <SectionHeader
        eyebrow="Pipeline"
        title="Runs"
        subtitle="History of plans and applies. Click a run to open its step timeline."
        action={
          <Button variant="secondary" size="sm" onClick={load}>
            Refresh
          </Button>
        }
      />

      {actionErr && (
        <div className="mb-4 flex items-start justify-between gap-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
          <span>{actionErr}</span>
          <button
            type="button"
            onClick={() => setActionErr(null)}
            className="shrink-0 font-medium text-red-500 hover:text-red-400"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* filter + status pills */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter by workspace, account, status, user…"
          className="max-w-sm"
        />
        <div className="flex gap-1">
          {(["all", "running", "planning", "planned", "awaiting_approval", "applying", "applied", "failed", "cancelled"] as const).map((s) => {
            const c = s === "all" ? runs.length : statusCounts[s] ?? 0;
            if (s !== "all" && c === 0) return null;
            return (
              <button
                key={s}
                onClick={() => setStatusFilter(s)}
                className={cx(
                  "rounded-md border px-2.5 py-1 text-xs font-medium transition-colors",
                  statusFilter === s
                    ? "border-sky-500 bg-sky-50 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300 dark:border-sky-700"
                    : "border-slate-200 text-slate-600 hover:bg-slate-50 dark:border-slate-700/70 dark:text-slate-400 dark:hover:bg-slate-800/60",
                )}
              >
                {s.replace(/_/g, " ")} <span className="ml-1 font-mono text-[10px] opacity-70">{c}</span>
              </button>
            );
          })}
        </div>
        <div className="ml-auto inline-flex rounded-md border border-slate-200 p-0.5 dark:border-slate-700/70">
          {(["time", "workspace"] as const).map((g) => (
            <button
              key={g}
              onClick={() => setGrouping(g)}
              className={cx(
                "rounded px-2.5 py-1 text-xs font-medium capitalize transition-colors",
                grouping === g ? "bg-sky-500 text-white" : "text-slate-600 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800",
              )}
            >
              by {g}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <Card key={i}>
              <CardBody>
                <Skeleton className="h-5 w-1/3" />
                <Skeleton className="mt-3 h-3 w-2/3" />
              </CardBody>
            </Card>
          ))}
        </div>
      ) : runs.length === 0 ? (
        <EmptyState title="No runs yet" description="Trigger a plan from the Dashboard to see runs here." />
      ) : filtered.length === 0 ? (
        <Card><CardBody className="text-sm text-slate-500">No runs match the current filter.</CardBody></Card>
      ) : (
        <div className="space-y-6">
          {groups.map((g) => (
            <section key={g.key}>
              <h3 className="mb-2 px-1 text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
                {g.label}{" "}
                <span className="ml-1 font-mono text-[10px] opacity-70">{g.runs.length}</span>
              </h3>
              <Card className="overflow-hidden">
                <ul className="divide-y divide-slate-200 dark:divide-slate-800/80">
                  {g.runs.map((run) => {
                    const ws = workspaces[run.workspace_id];
                    const u = run.triggered_by ? users[run.triggered_by] : undefined;
                    const triggerLabel = u?.email ?? (run.triggered_by?.startsWith("webhook:") ? run.triggered_by : run.triggered_by?.slice(0, 8) ?? "—");
                    const isOpen = openRunId === run.id;
                    return (
                      <li key={run.id} className={cx("transition-colors", isOpen && "bg-slate-50 dark:bg-slate-900/40") }>
                        <div className="flex flex-wrap items-start gap-3 px-5 py-4">
                          {/* left column: workspace context */}
                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="text-sm font-medium text-slate-900 dark:text-slate-100">
                                {ws ? (
                                  <Link to="/" className="hover:underline" title={ws.tf_working_dir || ws.name}>
                                    {(() => {
                                      // Disambiguate same-named leaves by showing the immediate
                                      // parent folder from tf_working_dir: e.g.
                                      //   `cloudflare-tunnel/home-aws` -> `cloudflare-tunnel › home-aws`.
                                      // Skip the prefix when it duplicates the account id or
                                      // region (which the mono line below already shows).
                                      const path = ws.tf_working_dir || "";
                                      const segs = path.split("/").filter(Boolean);
                                      const parent = segs.length >= 2 ? segs[segs.length - 2] : null;
                                      const noisy =
                                        !parent ||
                                        parent === ws.aws_account_id ||
                                        parent === ws.region ||
                                        parent === `account-${ws.aws_account_id}`;
                                      return noisy ? (
                                        ws.name
                                      ) : (
                                        <>
                                          <span className="text-slate-500 dark:text-slate-400">{parent}</span>
                                          <span className="mx-1 text-slate-400">›</span>
                                          {ws.name}
                                        </>
                                      );
                                    })()}
                                  </Link>
                                ) : (
                                  <span className="italic text-slate-500">deleted workspace</span>
                                )}
                              </span>
                              {ws && <Badge tone={ENV_TONE[ws.environment] ?? "neutral"}>{ws.environment}</Badge>}
                              {run.branch && (
                                <span
                                  className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-700 ring-1 ring-inset ring-slate-300/80 dark:bg-slate-800/80 dark:text-slate-300 dark:ring-slate-700/50"
                                  title={`branch: ${run.branch}`}
                                >
                                  <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
                                    <path d="M6 3a3 3 0 0 1 1 5.83V15a3 3 0 0 0 3 3h1V14a3 3 0 1 1 2 0v4h1a5 5 0 0 0 5-5V8.83A3 3 0 1 0 17 3a3 3 0 0 0 0 5.83V13a3 3 0 0 1-3 3h-1v-2a3 3 0 0 0-2-2.83V8.83A3 3 0 0 0 6 3Z" />
                                  </svg>
                                  <span className="font-mono">{run.branch}</span>
                                </span>
                              )}
                              <span className="rounded bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-700 ring-1 ring-inset ring-slate-200 dark:bg-slate-800/80 dark:text-slate-300 dark:ring-slate-700/50">
                                {run.command}
                              </span>
                              <RunStatusBadge status={run.status} />
                            </div>
                            <p className="mt-1 truncate font-mono text-[11px] text-slate-500">
                              {ws ? (
                                <>
                                  {ws.aws_account_id} · {ws.region}
                                  {ws.tf_working_dir && ws.tf_working_dir !== "." && (
                                    <> · {ws.tf_working_dir}</>
                                  )}
                                </>
                              ) : (
                                run.workspace_id
                              )}
                            </p>
                            <p className="mt-1 flex flex-wrap items-center gap-3 text-xs text-slate-500">
                              <span>
                                <span className="text-slate-400">id</span>{" "}
                                <Link
                                  to={`/runs/${run.id}`}
                                  className="font-mono text-sky-600 hover:underline dark:text-sky-400"
                                  title="Open this run on its own page"
                                >
                                  {run.id.slice(0, 8)}
                                </Link>
                              </span>
                              <span><span className="text-slate-400">by</span> {triggerLabel}</span>
                              {run.created_at && <span><span className="text-slate-400">started</span> {relTime(run.created_at)}</span>}
                              {durationOf(run) && <span><span className="text-slate-400">took</span> {durationOf(run)}</span>}
                            </p>
                          </div>
                          {/* right column: actions */}
                          <div className="flex shrink-0 flex-wrap items-center gap-2">
                            {hasMinRole(user, "operator") && run.status === "awaiting_approval" && (
                              <>
                                <Button
                                  size="sm"
                                  variant="warning"
                                  onClick={() => setApproveRunId(run.id)}
                                  disabled={busyCancel === run.id}
                                  title="Approve and run terraform apply"
                                >
                                  {busyCancel === run.id ? "…" : "✓ Approve"}
                                </Button>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={() => setRejectRunId(run.id)}
                                  disabled={busyCancel === run.id}
                                  className="text-red-500 hover:text-red-400"
                                  title="Reject and discard the plan"
                                >
                                  ✗ Reject
                                </Button>
                              </>
                            )}
                            {hasMinRole(user, "operator") && cancellable.has(run.status) && run.status !== "awaiting_approval" && (
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => setCancelRunId(run.id)}
                                disabled={busyCancel === run.id}
                                className="text-red-500 hover:text-red-400"
                              >
                                {busyCancel === run.id ? "Cancelling…" : "Cancel"}
                              </Button>
                            )}
                            {hasMinRole(user, "operator") && rerunnable.has(run.status) && (
                              <Button
                                size="sm"
                                variant="secondary"
                                onClick={() => rerun(run)}
                                disabled={!ws || busyRerun === run.id}
                                title={
                                  ws
                                    ? `Re-runs ${run.command} on this workspace's current branch`
                                    : "Workspace deleted — cannot re-run"
                                }
                              >
                                {busyRerun === run.id ? "…" : "↻ Re-run"}
                              </Button>
                            )}
                            <Button
                              size="sm"
                              variant={isOpen && openTab === "timeline" ? "primary" : "secondary"}
                              onClick={() => openTimeline(run.id)}
                            >
                              Timeline
                            </Button>
                            <Button
                              size="sm"
                              variant={isOpen && openTab === "plan" ? "primary" : "secondary"}
                              onClick={() => openPlan(run.id)}
                            >
                              Plan output
                            </Button>
                            <Button
                              size="sm"
                              variant="secondary"
                              onClick={() => setLogsRunId(run.id)}
                              title="View every step's output in one streaming log"
                            >
                              View all logs
                            </Button>
                          </div>
                        </div>
                        {/* error inline */}
                        {run.error_output && (
                          <pre data-testid="run-error" className="mx-5 mb-4 overflow-auto rounded-md border border-red-200 bg-red-50 p-3 text-xs text-red-800 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
                            {run.error_output}
                          </pre>
                        )}
                        {/* expanded detail */}
                        {isOpen && (
                          <div className="border-t border-slate-200 px-5 py-4 dark:border-slate-800/60">
                            {openTab === "timeline" && <RunSteps runId={run.id} />}
                            {openTab === "plan" && (
                              <pre
                                data-testid="run-logs"
                                className="max-h-80 overflow-auto rounded-md border border-slate-200 bg-white p-3 font-mono text-xs leading-relaxed text-slate-700 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300"
                              >
                                {planCache[run.id] ?? "(loading)"}
                              </pre>
                            )}
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </Card>
            </section>
          ))}
        </div>
      )}
      {logsRunId && (
        <RunLogsModal
          runId={logsRunId}
          label={logsRunId.slice(0, 8)}
          onClose={() => setLogsRunId(null)}
        />
      )}
      <ConfirmDialog
        open={approveRunId !== null}
        title="Approve run"
        message="An apply executor will be spawned with the saved plan. This will make real changes to your AWS account."
        confirmLabel="Approve & apply"
        cancelLabel="Cancel"
        tone="warning"
        busy={busyCancel === approveRunId}
        onConfirm={() => approveRunId && approveRun(approveRunId)}
        onCancel={() => setApproveRunId(null)}
      />
      <ConfirmDialog
        open={rejectRunId !== null}
        title="Reject run"
        message="Reject this run? The plan will be discarded."
        confirmLabel="Reject"
        cancelLabel="Cancel"
        tone="danger"
        busy={busyCancel === rejectRunId}
        onConfirm={() => rejectRunId && rejectRun(rejectRunId)}
        onCancel={() => setRejectRunId(null)}
      />
      <ConfirmDialog
        open={cancelRunId !== null}
        title="Cancel run"
        message="The executor will be asked to stop. In-flight terraform work may still partially apply before the worker exits."
        confirmLabel="Cancel run"
        cancelLabel="Keep running"
        tone="danger"
        busy={busyCancel === cancelRunId}
        onConfirm={() => cancelRunId && cancelRun(cancelRunId)}
        onCancel={() => setCancelRunId(null)}
      />
    </div>
  );
}
