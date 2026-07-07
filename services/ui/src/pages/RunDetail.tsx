import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import {
  Badge,
  Button,
  Card,
  CardBody,
  ConfirmDialog,
  EmptyState,
  RunStatusBadge,
  SectionHeader,
  Skeleton,
  cx,
} from "../components/ui";
import RunSteps from "../components/RunSteps";
import RunLogsModal from "../components/RunLogsModal";
import PolicyResults, { PolicyStatusBadge } from "../components/PolicyResults";
import { useCurrentUser, hasMinRole } from "../hooks/useAuth";

type Run = {
  id: string;
  workspace_id: string;
  status: string;
  command: string;
  branch?: string | null;
  plan_output?: string | null;
  error_output?: string | null;
  policy_status?: string | null;
  triggered_by?: string | null;
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
  repo_ref?: string;
  // "terraform" (default) or "helm".
  kind?: string;
};

type Tab = "timeline" | "plan" | "policies";
const REFRESH_MS = 8_000;

const ENV_TONE: Record<string, "success" | "warning" | "info" | "neutral" | "amber" | "violet"> = {
  prod: "warning",
  preprod: "violet",
  staging: "info",
  dev: "success",
  shared: "neutral",
};

function relTime(iso?: string | null): string {
  if (!iso) return "—";
  const sec = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return new Date(iso).toLocaleString();
}

export default function RunDetail() {
  const { id = "" } = useParams<{ id: string }>();
  const nav = useNavigate();
  const user = useCurrentUser();
  const [run, setRun] = useState<Run | null>(null);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [planOutput, setPlanOutput] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("timeline");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [linkCopied, setLinkCopied] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const [approveOpen, setApproveOpen] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);

  async function load(silent = false) {
    try {
      const r = await api.get(`/v1/runs/${id}`);
      setRun(r.data);
      // Fetch workspace once (it doesn't change often) — needed for re-run.
      if (!workspace || workspace.id !== r.data.workspace_id) {
        try {
          const w = await api.get(`/v1/workspaces/${r.data.workspace_id}`);
          setWorkspace(w.data);
        } catch {
          // workspace deleted — re-run will be disabled
        }
      }
    } catch (e: any) {
      if (e?.response?.status === 404) setNotFound(true);
      else if (!silent) setErr(e?.response?.data?.detail ?? e?.message ?? "Load failed");
    }
  }

  useEffect(() => {
    setNotFound(false);
    setRun(null);
    setWorkspace(null);
    setPlanOutput(null);
    void load();
    const t = window.setInterval(() => {
      if (document.visibilityState === "visible") void load(true);
    }, REFRESH_MS);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  async function loadPlan() {
    if (planOutput !== null) return;
    try {
      const r = await api.get(`/v1/runs/${id}/plan`);
      setPlanOutput(r.data.plan_output || "(no plan output yet)");
    } catch {
      setPlanOutput("(failed to load plan)");
    }
  }

  useEffect(() => {
    if (tab === "plan") void loadPlan();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  async function approve() {
    if (!run) return;
    setApproveOpen(false);
    setBusy("approve");
    setErr(null);
    try {
      await api.post(`/v1/runs/${id}/approve`, { comment: "Approved from run detail" });
      await load();
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Approve failed");
    } finally {
      setBusy(null);
    }
  }

  async function reject() {
    if (!run) return;
    setRejectOpen(false);
    setBusy("reject");
    setErr(null);
    try {
      await api.post(`/v1/runs/${id}/reject`, { comment: "Rejected from run detail" });
      await load();
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Reject failed");
    } finally {
      setBusy(null);
    }
  }

  async function cancel() {
    if (!run) return;
    setCancelOpen(false);
    setBusy("cancel");
    setErr(null);
    try {
      await api.post(`/v1/runs/${id}/cancel`);
      await load();
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Cancel failed");
    } finally {
      setBusy(null);
    }
  }

  async function rerun() {
    if (!run || !workspace) return;
    // Per team rule: re-run uses the workspace's CURRENT branch, never the
    // original run's branch. The trigger endpoint reads workspace.repo_ref
    // server-side, so we just POST `command` and the API does the right thing.
    setBusy("rerun");
    setErr(null);
    try {
      const r = await api.post(`/v1/workspaces/${workspace.id}/runs`, { command: run.command });
      nav(`/runs/${r.data.id}`);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Re-run failed");
      setBusy(null);
    }
  }

  function copyLink() {
    const url = `${window.location.origin}/runs/${id}`;
    navigator.clipboard.writeText(url).then(() => {
      setLinkCopied(true);
      window.setTimeout(() => setLinkCopied(false), 1500);
    }).catch(() => {});
  }

  if (notFound) {
    return (
      <div>
        <SectionHeader title="Run not found" subtitle={`No run with id ${id.slice(0, 8)}…`} />
        <EmptyState
          title="This run does not exist"
          description="It may have been deleted, or the link is wrong."
        />
        <div className="mt-4">
          <Link to="/runs" className="text-sm text-sky-600 hover:underline">← Back to all runs</Link>
        </div>
      </div>
    );
  }

  if (!run) {
    return (
      <div>
        <SectionHeader title="Run" subtitle="Loading…" />
        <Card><CardBody><Skeleton className="h-5 w-1/3" /><Skeleton className="mt-3 h-3 w-2/3" /></CardBody></Card>
      </div>
    );
  }

  // `planned` is the terminal state of a plan-only run — the executor has
  // already exited, so there's nothing to cancel. (Apply runs never rest at
  // `planned`; they go straight to `awaiting_approval`.)
  const cancellable = new Set(["pending", "running", "planning", "awaiting_approval"]);
  const isAwaitingApproval = run.status === "awaiting_approval";
  const tabs: Tab[] = ["timeline", "plan", "policies"];

  return (
    <div>
      <SectionHeader
        title={`Run ${run.id.slice(0, 8)}`}
        subtitle={
          workspace ? (
            <span>
              <Link to="/" className="hover:underline">{workspace.name}</Link>{" "}
              · <span className="font-mono">{workspace.aws_account_id}</span>{" "}
              · <span className="font-mono">{workspace.region}</span>
            </span>
          ) : "Workspace deleted."
        }
        action={
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="ghost" onClick={copyLink}>
              {linkCopied ? "✓ Copied" : "Copy link"}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => setLogsOpen(true)}
              title="View every step's output in one streaming log"
            >
              View all logs
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={rerun}
              disabled={!workspace || !!busy}
              title={
                workspace
                  ? `Re-runs ${run.command} on this workspace's current branch (${workspace.repo_ref || "main"})`
                  : "Workspace deleted — cannot re-run"
              }
            >
              {busy === "rerun" ? "…" : "↻ Re-run"}
            </Button>
          </div>
        }
      />

      <Card className="mb-4">
        <CardBody>
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700 ring-1 ring-inset ring-slate-200 dark:bg-slate-800/80 dark:text-slate-300 dark:ring-slate-700/50">
              {run.command}
            </span>
            <RunStatusBadge status={run.status} />
            <PolicyStatusBadge status={run.policy_status} />
            {workspace && (
              <Badge tone={ENV_TONE[workspace.environment] ?? "neutral"}>{workspace.environment}</Badge>
            )}
            {run.branch && (
              <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-700 ring-1 ring-inset ring-slate-300/80 dark:bg-slate-800/80 dark:text-slate-300 dark:ring-slate-700/50">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
                  <path d="M6 3a3 3 0 0 1 1 5.83V15a3 3 0 0 0 3 3h1V14a3 3 0 1 1 2 0v4h1a5 5 0 0 0 5-5V8.83A3 3 0 1 0 17 3a3 3 0 0 0 0 5.83V13a3 3 0 0 1-3 3h-1v-2a3 3 0 0 0-2-2.83V8.83A3 3 0 0 0 6 3Z" />
                </svg>
                <span className="font-mono">{run.branch}</span>
              </span>
            )}
            <span className="ml-auto text-xs text-slate-500">
              created {relTime(run.created_at)}
              {run.completed_at && <> · completed {relTime(run.completed_at)}</>}
            </span>
          </div>

          {run.error_output && (
            <pre data-testid="run-error" className="mt-3 overflow-auto rounded-md border border-red-900/40 bg-red-950/30 p-3 text-xs text-red-300">
              {run.error_output}
            </pre>
          )}

          {(isAwaitingApproval || cancellable.has(run.status)) && hasMinRole(user, "operator") && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {isAwaitingApproval && (
                <>
                  <Button
                    size="sm"
                    variant="warning"
                    onClick={() => setApproveOpen(true)}
                    disabled={!!busy}
                    title="Approve and run terraform apply"
                  >
                    {busy === "approve" ? "…" : "✓ Approve"}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setRejectOpen(true)}
                    disabled={!!busy}
                    className="text-red-500 hover:text-red-400"
                    title="Reject and discard the plan"
                  >
                    ✗ Reject
                  </Button>
                </>
              )}
              {!isAwaitingApproval && cancellable.has(run.status) && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setCancelOpen(true)}
                  disabled={!!busy}
                  className="text-red-500 hover:text-red-400"
                >
                  {busy === "cancel" ? "…" : "Cancel"}
                </Button>
              )}
            </div>
          )}

          {err && (
            <p className="mt-2 rounded-md border border-red-200 bg-red-50 px-2.5 py-1.5 text-xs text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
              {err}
            </p>
          )}
        </CardBody>
      </Card>

      <div className="mb-3 inline-flex rounded-md border border-slate-200 p-0.5 dark:border-slate-700/70">
        {tabs.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cx(
              "rounded px-3 py-1 text-xs font-medium capitalize transition-colors",
              tab === t
                ? "bg-sky-500 text-white"
                : "text-slate-600 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800",
            )}
          >
            {t}
          </button>
        ))}
      </div>

      <Card>
        <CardBody>
          {tab === "timeline" && (
            <RunSteps
              runId={run.id}
              approval={
                isAwaitingApproval && hasMinRole(user, "operator")
                  ? {
                      visible: true,
                      busy,
                      onApprove: () => setApproveOpen(true),
                      onReject: () => setRejectOpen(true),
                    }
                  : undefined
              }
            />
          )}
          {tab === "plan" && (
            <pre
              data-testid="run-logs"
              className="max-h-96 overflow-auto rounded-md border border-slate-200 bg-white p-3 font-mono text-xs leading-relaxed text-slate-700 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300"
            >
              {planOutput ?? "(loading)"}
            </pre>
          )}
          {tab === "policies" && <PolicyResults runId={run.id} />}
        </CardBody>
      </Card>

      {logsOpen && (
        <RunLogsModal
          runId={run.id}
          label={run.id.slice(0, 8)}
          onClose={() => setLogsOpen(false)}
        />
      )}
      <ConfirmDialog
        open={approveOpen}
        title="Approve run"
        message="An apply executor will be spawned with the saved plan. This will make real changes to your AWS account."
        confirmLabel="Approve & apply"
        cancelLabel="Cancel"
        tone="warning"
        busy={busy === "approve"}
        onConfirm={approve}
        onCancel={() => setApproveOpen(false)}
      />
      <ConfirmDialog
        open={rejectOpen}
        title="Reject run"
        message="Reject this run? The plan will be discarded."
        confirmLabel="Reject"
        cancelLabel="Cancel"
        tone="danger"
        busy={busy === "reject"}
        onConfirm={reject}
        onCancel={() => setRejectOpen(false)}
      />
      <ConfirmDialog
        open={cancelOpen}
        title="Cancel run"
        message="The executor will be asked to stop. In-flight terraform work may still partially apply before the worker exits."
        confirmLabel="Cancel run"
        cancelLabel="Keep running"
        tone="danger"
        busy={busy === "cancel"}
        onConfirm={cancel}
        onCancel={() => setCancelOpen(false)}
      />
    </div>
  );
}
