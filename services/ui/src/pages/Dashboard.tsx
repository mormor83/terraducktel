import { FormEvent, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useCurrentUser, hasMinRole } from "../hooks/useAuth";

/**
 * Display name for the welcome line. Prefer the `name` claim from the JWT
 * (populated from OIDC for SSO users via users.display_name), and fall back
 * to a prettified email local part so local-auth users still get a friendly
 * label (`admin@test.com` → `Admin`).
 */
function welcomeName(user: { name: string | null; email: string } | null): string {
  if (!user) return "";
  if (user.name) return user.name;
  const local = (user.email.split("@")[0] || "").trim();
  if (!local) return "";
  return local
    .split(/[._-]+/)
    .filter(Boolean)
    .map((p) => p[0].toUpperCase() + p.slice(1).toLowerCase())
    .join(" ");
}
import GitImport from "../components/GitImport";
import WorkspaceTree, {
  type AwsAccountLite,
  type AzureSubscriptionLite,
  type GcpProjectLite,
  type Run,
  type Workspace,
} from "../components/WorkspaceTree";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  cx,
  EmptyState,
  Input,
  Label,
  SectionHeader,
  Select,
  Skeleton,
  Stat,
} from "../components/ui";

function CreateWorkspaceForm({ onCreated, onCancel }: { onCreated: () => void; onCancel: () => void }) {
  const [name, setName] = useState("");
  const [env, setEnv] = useState("dev");
  const [region, setRegion] = useState("us-east-1");
  const [accountId, setAccountId] = useState("");
  const [repoUrl, setRepoUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await api.post("/v1/workspaces", {
        name,
        environment: env,
        region,
        aws_account_id: accountId || "000000000000",
        repo_url: repoUrl || undefined,
      });
      onCreated();
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Failed to create workspace");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card className="mb-6">
      <CardHeader>
        <CardTitle>New workspace</CardTitle>
        <Button variant="ghost" size="sm" type="button" onClick={onCancel}>
          Close
        </Button>
      </CardHeader>
      <CardBody>
        <form onSubmit={onSubmit} className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <Label>Name</Label>
              <Input value={name} onChange={(e) => setName(e.target.value)} required placeholder="my-infra" />
            </div>
            <div>
              <Label>Environment</Label>
              <Select value={env} onChange={(e) => setEnv(e.target.value)}>
                <option value="dev">dev</option>
                <option value="staging">staging</option>
                <option value="prod">prod</option>
              </Select>
            </div>
            <div>
              <Label>Region</Label>
              <Input value={region} onChange={(e) => setRegion(e.target.value)} />
            </div>
            <div>
              <Label>AWS Account ID</Label>
              <Input value={accountId} onChange={(e) => setAccountId(e.target.value)} placeholder="000000000000" />
            </div>
            <div className="sm:col-span-2">
              <Label>Repo URL (optional)</Label>
              <Input value={repoUrl} onChange={(e) => setRepoUrl(e.target.value)} placeholder="https://github.com/org/repo.git" />
            </div>
          </div>
          {error && <p className="text-sm text-red-400">{error}</p>}
          <div className="flex gap-2">
            <Button type="submit" disabled={submitting}>
              {submitting ? "Creating…" : "Create workspace"}
            </Button>
            <Button type="button" variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}


const REFRESH_INTERVAL_MS = 15_000;
const TIP_DISMISSED_KEY = "terraducktel_tip_runs_dismissed";

const ACTIVE_RUN_STATUSES = new Set([
  "pending", "running", "planning", "planned", "applying", "awaiting_approval",
]);

function fmtDuration(sec: number | null): string {
  if (sec == null) return "—";
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.round(sec / 60)}m`;
  return `${(sec / 3600).toFixed(1)}h`;
}

type EnvHealth = { env: string; total: number; drifted: number; failures: number };

/** Fleet-health KPIs derived entirely from the already-fetched runs + workspaces. */
export function computeFleetHealth(runs: Run[], workspaces: Workspace[]) {
  const wsById = new Map(workspaces.map((w) => [w.id, w]));
  const now = Date.now();
  const WEEK = 7 * 24 * 3600 * 1000;
  const inWindow = (r: Run) => !!r.created_at && now - new Date(r.created_at).getTime() <= WEEK;

  let applied = 0, failed = 0, active = 0;
  const durations: number[] = [];
  const failed_runs: Run[] = [];
  for (const r of runs) {
    if (ACTIVE_RUN_STATUSES.has(r.status)) active++;
    if (inWindow(r)) {
      if (r.status === "applied") applied++;
      else if (r.status === "failed") failed++;
    }
    if (r.status === "failed") failed_runs.push(r);
    if (r.started_at && r.completed_at) {
      const d = (new Date(r.completed_at).getTime() - new Date(r.started_at).getTime()) / 1000;
      if (d > 0 && d < 24 * 3600) durations.push(d);
    }
  }
  const terminal = applied + failed;
  const recent = durations.slice(-20);
  const avgDuration = recent.length ? recent.reduce((a, b) => a + b, 0) / recent.length : null;

  failed_runs.sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
  const recentFailures = failed_runs.slice(0, 5).map((r) => ({ run: r, ws: wsById.get(r.workspace_id) }));

  const envMap = new Map<string, EnvHealth>();
  for (const w of workspaces) {
    const e = w.environment || "—";
    const cur = envMap.get(e) ?? { env: e, total: 0, drifted: 0, failures: 0 };
    cur.total++;
    if (w.drift_status === "drifted") cur.drifted++;
    envMap.set(e, cur);
  }
  for (const r of runs) {
    if (r.status === "failed" && inWindow(r)) {
      const e = wsById.get(r.workspace_id)?.environment || "—";
      const cur = envMap.get(e);
      if (cur) cur.failures++;
    }
  }
  const byEnv = [...envMap.values()].sort((a, b) => a.env.localeCompare(b.env));

  return {
    successRate: terminal ? Math.round((100 * applied) / terminal) : null,
    terminal,
    active,
    avgDuration,
    recentFailures,
    byEnv,
  };
}

export default function Dashboard() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [awsAccounts, setAwsAccounts] = useState<AwsAccountLite[]>([]);
  const [azureSubscriptions, setAzureSubscriptions] = useState<AzureSubscriptionLite[]>([]);
  const [gcpProjects, setGcpProjects] = useState<GcpProjectLite[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [tipDismissed, setTipDismissed] = useState(
    () => localStorage.getItem(TIP_DISMISSED_KEY) === "1",
  );
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState<string | null>(null);
  const [dashTab, setDashTab] = useState<"overview" | "fleet">("overview");
  const user = useCurrentUser();

  async function runRepoSync() {
    setSyncing(true);
    setSyncResult(null);
    try {
      const r = await api.post("/v1/workspaces/sync");
      const { checked, ok, orphaned, skipped, errors } = r.data;
      const errSuffix = errors?.length ? ` · ${errors.length} error${errors.length === 1 ? "" : "s"}` : "";
      setSyncResult(
        `Synced ${checked} workspaces — ok: ${ok}, orphaned: ${orphaned}, skipped: ${skipped}${errSuffix}`,
      );
      await load({ silent: true });
    } catch (e: any) {
      setSyncResult(e?.response?.data?.detail ?? e?.message ?? "Sync failed");
    } finally {
      setSyncing(false);
    }
  }

  async function load(opts: { silent?: boolean } = {}) {
    if (opts.silent) setRefreshing(true);
    else setLoading(true);
    try {
      const [w, r, a, az, gc] = await Promise.all([
        api.get("/v1/workspaces"),
        api.get("/v1/runs"),
        // Admin-gated; viewers may 403 — render account rows without display names then.
        api.get("/v1/aws-accounts").catch(() => ({ data: [] })),
        // Same: used to label Azure subscription groups + the link selector.
        api.get("/v1/azure-subscriptions").catch(() => ({ data: [] })),
        // Same: used to label GCP project groups + the state-backend selector.
        api.get("/v1/gcp-projects").catch(() => ({ data: [] })),
      ]);
      setWorkspaces(w.data);
      setRuns(r.data);
      setAwsAccounts(a.data);
      setAzureSubscriptions(az.data);
      setGcpProjects(gc.data);
      setLastRefreshed(new Date());
      setErr(null);
    } catch (e: any) {
      setErr(e?.message ?? "failed to load");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  // Auto-refresh while the tab is visible. Pauses when the tab goes hidden so
  // we don't hammer the API in background tabs, and refreshes immediately on
  // re-focus so a returning user sees fresh state.
  useEffect(() => {
    if (!autoRefresh) return;
    let timer: number | null = null;
    function start() {
      stop();
      timer = window.setInterval(() => {
        if (document.visibilityState === "visible") void load({ silent: true });
      }, REFRESH_INTERVAL_MS);
    }
    function stop() {
      if (timer !== null) {
        window.clearInterval(timer);
        timer = null;
      }
    }
    function onVisibility() {
      if (document.visibilityState === "visible") {
        void load({ silent: true });
        start();
      } else {
        stop();
      }
    }
    document.addEventListener("visibilitychange", onVisibility);
    start();
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      stop();
    };
  }, [autoRefresh]);

  function formatRelative(d: Date | null): string {
    if (!d) return "";
    const sec = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
    if (sec < 5) return "just now";
    if (sec < 60) return `${sec}s ago`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}m ago`;
    return d.toLocaleTimeString();
  }

  // Stats derived from data
  const total = workspaces.length;
  const drifted = workspaces.filter((w) => w.drift_status === "drifted").length;
  const awaiting = runs.filter((r) => r.status === "awaiting_approval").length;
  const health = useMemo(() => computeFleetHealth(runs, workspaces), [runs, workspaces]);

  return (
    <div data-testid="workspace-list">
      <SectionHeader
        eyebrow="Overview"
        title={`Welcome${user ? `, ${welcomeName(user)}` : ""}`}
        subtitle={
          <>
            Manage Terraform workspaces, plan and apply changes, and watch for drift.{" "}
            {lastRefreshed && (
              <span className="text-slate-400 dark:text-slate-500">
                · refreshed {formatRelative(lastRefreshed)}
              </span>
            )}
          </>
        }
        action={
          <div className="flex flex-wrap items-center gap-2">
            <label
              className="flex select-none items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400"
              title="Auto-refresh every 15s while this tab is in the foreground"
            >
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)}
                className="h-3.5 w-3.5 rounded border-slate-300 text-sky-600 focus:ring-sky-500 dark:border-slate-600 dark:bg-slate-900"
              />
              auto
            </label>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => load({ silent: true })}
              disabled={refreshing}
              title="Reload list data from TDT (workspaces, runs, AWS accounts). Does NOT re-scan the Git repo — use Sync from repo for that."
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="currentColor"
                className={refreshing ? "animate-spin" : ""}
                aria-hidden
              >
                <path d="M17.65 6.35A8 8 0 1 0 19.73 14H17.6a6 6 0 1 1-1.42-6.22L13 11h7V4l-2.35 2.35Z" />
              </svg>
              {refreshing ? "Reloading…" : "Reload data"}
            </Button>
            {hasMinRole(user, "admin") && (
              <Button
                variant="secondary"
                size="sm"
                onClick={runRepoSync}
                disabled={syncing}
                title="Re-clone each workspace's tracked branch and mark any whose source path is missing as orphaned."
              >
                {syncing ? "Syncing…" : "Sync from repo"}
              </Button>
            )}
            {hasMinRole(user, "admin") && !showCreate && !showImport && (
              <>
                <Button variant="secondary" onClick={() => setShowImport(true)}>
                  Import from Git
                </Button>
                <Button onClick={() => setShowCreate(true)}>+ New workspace</Button>
              </>
            )}
          </div>
        }
      />

      {showCreate && hasMinRole(user, "admin") && (
        <CreateWorkspaceForm
          onCreated={() => {
            setShowCreate(false);
            load();
          }}
          onCancel={() => setShowCreate(false)}
        />
      )}

      {showImport && hasMinRole(user, "admin") && (
        <div className="mb-6">
          <div className="mb-3 flex justify-end">
            <Button variant="ghost" size="sm" onClick={() => setShowImport(false)}>
              Close
            </Button>
          </div>
          <GitImport onImported={() => load()} />
        </div>
      )}

      {/* Overview / Fleet-health tabs (Overview default) */}
      <div className="mb-6">
        <div className="mb-4 flex gap-1 border-b border-brand-border" role="tablist">
          {([["overview", "Overview"], ["fleet", "Fleet health"]] as const).map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={dashTab === key}
              onClick={() => setDashTab(key)}
              className={cx(
                "-mb-px border-b-2 px-4 py-2 text-sm font-medium transition-colors",
                dashTab === key
                  ? "border-brand-500 text-brand-700 dark:border-brand-400 dark:text-brand-200"
                  : "border-transparent text-brand-muted hover:text-brand-text",
              )}
            >
              {label}
            </button>
          ))}
        </div>

        {dashTab === "overview" ? (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <Stat label="Workspaces" value={total} hint={total === 1 ? "1 active" : `${total} active`} />
            <Stat
              label="Drifted"
              value={drifted}
              hint={drifted === 0 ? "all clean" : "view in inventory"}
              tone={drifted > 0 ? "danger" : "success"}
              to="/inventory?status=drifted"
            />
            <Stat
              label="Awaiting approval"
              value={awaiting}
              hint={awaiting === 0 ? "none pending" : "review in Runs"}
              tone={awaiting > 0 ? "amber" : "neutral"}
              to="/runs?status=awaiting_approval"
            />
          </div>
        ) : (
          <div>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <Stat
              label="Run success (7d)"
              value={health.successRate == null ? "—" : `${health.successRate}%`}
              hint={health.terminal === 0 ? "no completed runs" : `${health.terminal} completed`}
              tone={
                health.successRate == null
                  ? "neutral"
                  : health.successRate >= 90
                  ? "success"
                  : health.successRate >= 70
                  ? "amber"
                  : "danger"
              }
            />
            <Stat
              label="Active runs"
              value={health.active}
              hint={health.active === 0 ? "idle" : "in flight"}
              tone={health.active > 0 ? "info" : "neutral"}
              to="/runs"
            />
            <Stat
              label="Avg run time"
              value={fmtDuration(health.avgDuration)}
              hint="recent completed"
            />
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            {/* Recent failures feed */}
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Recent failures</CardTitle>
                {health.recentFailures.length > 0 && (
                  <Link to="/runs?status=failed" className="text-xs text-brand-600 hover:underline dark:text-brand-300">
                    View all
                  </Link>
                )}
              </CardHeader>
              <CardBody className="p-0">
                {health.recentFailures.length === 0 ? (
                  <p className="px-5 py-6 text-center text-sm text-brand-muted">No recent failures 🎉</p>
                ) : (
                  <ul className="divide-y divide-brand-border/70">
                    {health.recentFailures.map(({ run, ws }) => (
                      <li key={run.id}>
                        <Link
                          to={`/runs/${run.id}`}
                          className="flex items-center gap-3 px-5 py-2.5 transition-colors hover:bg-brand-surface2/70 dark:hover:bg-white/5"
                        >
                          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-red-500" aria-hidden />
                          <span className="min-w-0 flex-1 truncate text-sm text-brand-text">
                            {ws?.name ?? run.workspace_id.slice(0, 8)}
                            <span className="ml-2 font-mono text-[11px] text-brand-muted">{run.command}</span>
                          </span>
                          <span className="shrink-0 text-[11px] text-brand-muted">{formatRelative(run.created_at ? new Date(run.created_at) : null)}</span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </CardBody>
            </Card>

            {/* Per-environment health */}
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Health by environment</CardTitle>
              </CardHeader>
              <CardBody className="p-0">
                <ul className="divide-y divide-brand-border/70">
                  {health.byEnv.map((e) => (
                    <li key={e.env} className="flex items-center justify-between gap-3 px-5 py-2.5 text-sm">
                      <span className="font-medium text-brand-text">{e.env}</span>
                      <div className="flex items-center gap-2">
                        <Badge tone="neutral">{e.total} ws</Badge>
                        <Badge tone={e.drifted > 0 ? "danger" : "success"}>
                          {e.drifted > 0 ? `${e.drifted} drifted` : "no drift"}
                        </Badge>
                        {e.failures > 0 && <Badge tone="amber">{e.failures} failed</Badge>}
                      </div>
                    </li>
                  ))}
                </ul>
              </CardBody>
            </Card>
          </div>
          </div>
        )}
      </div>

      {!tipDismissed && (
        <Card className="mb-4 border-sky-200 bg-sky-50 dark:border-sky-900/40 dark:bg-sky-950/20">
          <CardBody className="flex items-start gap-3 text-sm">
            <span className="mt-0.5 text-sky-600 dark:text-sky-400" aria-hidden>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2Zm1 15h-2v-6h2Zm0-8h-2V7h2Z" />
              </svg>
            </span>
            <div className="flex-1 text-slate-700 dark:text-slate-300">
              <p className="font-medium text-slate-900 dark:text-slate-100">How runs work</p>
              <p className="mt-1">
                Click <strong>Run</strong> on a workspace to start the unified flow: clone repo → checkov → plan → cost
                estimation → <strong>pause for approval</strong>. Any operator clicks ✓ Approve right inside the
                run row in <strong>Runs</strong>, and the apply phase resumes against the exact plan that was reviewed.
              </p>
            </div>
            <button
              type="button"
              onClick={() => {
                localStorage.setItem(TIP_DISMISSED_KEY, "1");
                setTipDismissed(true);
              }}
              className="ml-2 text-xs text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"
              title="Hide this tip (persists per browser)"
            >
              Dismiss
            </button>
          </CardBody>
        </Card>
      )}

      {syncResult && (
        <Card className="mb-4 border-sky-200 bg-sky-50 dark:border-sky-900/40 dark:bg-sky-950/20">
          <CardBody className="flex items-start justify-between gap-3 text-sm text-slate-700 dark:text-slate-300">
            <span>{syncResult}</span>
            <button
              type="button"
              onClick={() => setSyncResult(null)}
              className="text-xs text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"
            >
              Dismiss
            </button>
          </CardBody>
        </Card>
      )}

      {err && (
        <Card className="mb-4 border-red-900/50 bg-red-950/30">
          <CardBody className="text-sm text-red-300">⚠ {err}</CardBody>
        </Card>
      )}

      {loading ? (
        <Card>
          <CardBody className="space-y-3">
            <Skeleton className="h-5 w-1/3" />
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-4 w-1/2" />
          </CardBody>
        </Card>
      ) : workspaces.length === 0 ? (
        <EmptyState
          title="No workspaces yet"
          description={
            hasMinRole(user, "admin")
              ? "Create one to start running Terraform plans and applies."
              : "Ask an admin to create a workspace for you."
          }
          action={
            hasMinRole(user, "admin") && (
              <Button onClick={() => setShowCreate(true)}>+ Create workspace</Button>
            )
          }
        />
      ) : (
        <WorkspaceTree
          workspaces={workspaces}
          runs={runs}
          awsAccounts={awsAccounts}
          azureSubscriptions={azureSubscriptions}
          gcpProjects={gcpProjects}
          onChanged={load}
        />
      )}
    </div>
  );
}
