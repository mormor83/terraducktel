// Top-level workspace tree: filter + expand/collapse controls, classifies each
// workspace into an AWS-account or Azure-subscription group, and renders both.
// The row/group implementation lives in ./workspace-tree/* — this file is the
// public entry point and orchestrates grouping.
import { useMemo, useState } from "react";
import { Card, Input } from "./ui";
import { AccountGroup, AzureSubscriptionGroup, GcpProjectGroup } from "./workspace-tree/groups";
import { azureInfo, gcpInfo, workspacePathSegments } from "./workspace-tree/paths";
import type {
  AwsAccountLite,
  AzureSubscriptionLite,
  ExpandSignal,
  GcpProjectLite,
  Run,
  Workspace,
} from "./workspace-tree/types";

// Public surface preserved for existing importers (Dashboard, Settings).
export type { AwsAccountLite, AzureSubscriptionLite, GcpProjectLite, Run, Workspace };
export { azureInfo, gcpInfo, workspacePathSegments };

export default function WorkspaceTree({
  workspaces,
  runs,
  awsAccounts,
  azureSubscriptions,
  gcpProjects,
  onChanged,
}: {
  workspaces: Workspace[];
  runs: Run[];
  awsAccounts: AwsAccountLite[];
  azureSubscriptions: AzureSubscriptionLite[];
  gcpProjects: GcpProjectLite[];
  onChanged: () => void;
}) {
  const [filter, setFilter] = useState("");
  const [expandSignal, setExpandSignal] = useState<ExpandSignal>(null);

  const accountNameById = useMemo(() => {
    const m = new Map<string, string>();
    for (const a of awsAccounts) m.set(a.account_id, a.name);
    return m;
  }, [awsAccounts]);

  // Two views of the registered Azure subscriptions: by TDT pk (what the
  // explicit `azure_subscription_id` link stores) and by Azure GUID (what the
  // `azure/subscription-<guid>/` repo path encodes). The link wins; the path
  // is the fallback so a synced-but-unlinked workspace still groups correctly.
  const azureByPk = useMemo(() => {
    const m = new Map<string, AzureSubscriptionLite>();
    for (const s of azureSubscriptions) m.set(s.id, s);
    return m;
  }, [azureSubscriptions]);
  const azureByGuid = useMemo(() => {
    const m = new Map<string, AzureSubscriptionLite>();
    for (const s of azureSubscriptions) m.set(s.subscription_id, s);
    return m;
  }, [azureSubscriptions]);

  // Same two views for GCP projects: by TDT pk (the explicit link) and by GCP
  // project id (what the `gcp/project-<id>/` repo path encodes).
  const gcpByPk = useMemo(() => {
    const m = new Map<string, GcpProjectLite>();
    for (const p of gcpProjects) m.set(p.id, p);
    return m;
  }, [gcpProjects]);
  const gcpByProjectId = useMemo(() => {
    const m = new Map<string, GcpProjectLite>();
    for (const p of gcpProjects) m.set(p.project_id, p);
    return m;
  }, [gcpProjects]);

  // Classify a workspace into its top-level cloud group + the region it should
  // nest under. Detection: explicit link first (Azure, then GCP), then the
  // path convention (Azure, then GCP); AWS is the default.
  function classify(w: Workspace): {
    cloud: "aws" | "azure" | "gcp";
    key: string;
    region: string;
  } {
    if (w.azure_subscription_id) {
      const sub = azureByPk.get(w.azure_subscription_id);
      const info = azureInfo(w);
      return {
        cloud: "azure",
        // Group under the matched sub's pk so link + path agree on one key.
        key: sub ? sub.id : w.azure_subscription_id,
        region: info?.region ?? w.region,
      };
    }
    if (w.gcp_project_id) {
      const proj = gcpByPk.get(w.gcp_project_id);
      const info = gcpInfo(w);
      return {
        cloud: "gcp",
        key: proj ? proj.id : w.gcp_project_id,
        region: info?.region ?? w.region,
      };
    }
    const info = azureInfo(w);
    if (info) {
      const sub = azureByGuid.get(info.guid);
      return { cloud: "azure", key: sub ? sub.id : `guid:${info.guid}`, region: info.region };
    }
    const ginfo = gcpInfo(w);
    if (ginfo) {
      const proj = gcpByProjectId.get(ginfo.projectId);
      return { cloud: "gcp", key: proj ? proj.id : `pid:${ginfo.projectId}`, region: ginfo.region };
    }
    return { cloud: "aws", key: w.aws_account_id, region: w.region };
  }

  // Latest run per workspace, filtered to runs that match the workspace's
  // currently-tracked branch. No fallback — if there are no runs on `repo_ref`
  // the leaf shows "no runs on <branch>" instead of leaking another branch's
  // status. Pre-012 runs (NULL branch) are intentionally ignored.
  const wsById = useMemo(() => {
    const m = new Map<string, Workspace>();
    for (const w of workspaces) m.set(w.id, w);
    return m;
  }, [workspaces]);

  const latestByWs = useMemo(() => {
    const sorted = runs.slice().sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? ""));
    sorted.reverse();
    const map = new Map<string, Run>();
    for (const r of sorted) {
      if (map.has(r.workspace_id)) continue;
      const ws = wsById.get(r.workspace_id);
      const wsBranch = ws?.repo_ref || "main";
      if ((r.branch || "") !== wsBranch) continue;
      map.set(r.workspace_id, r);
    }
    return map;
  }, [runs, wsById]);

  const filtered = useMemo(() => {
    if (!filter.trim()) return workspaces;
    const q = filter.trim().toLowerCase();
    return workspaces.filter((w) => {
      const sub = w.azure_subscription_id
        ? azureByPk.get(w.azure_subscription_id)
        : azureByGuid.get(azureInfo(w)?.guid ?? "");
      const proj = w.gcp_project_id
        ? gcpByPk.get(w.gcp_project_id)
        : gcpByProjectId.get(gcpInfo(w)?.projectId ?? "");
      return [
        w.name,
        w.aws_account_id,
        w.region,
        w.environment,
        w.tf_working_dir ?? "",
        accountNameById.get(w.aws_account_id) ?? "",
        sub?.name ?? "",
        sub?.subscription_id ?? azureInfo(w)?.guid ?? "",
        proj?.name ?? "",
        proj?.project_id ?? gcpInfo(w)?.projectId ?? "",
      ]
        .join(" ")
        .toLowerCase()
        .includes(q);
    });
  }, [workspaces, filter, accountNameById, azureByPk, azureByGuid, gcpByPk, gcpByProjectId]);

  // Group into AWS accounts, Azure subscriptions, and GCP projects; each →
  // region → workspaces[].
  const { awsGrouped, azureGrouped, gcpGrouped } = useMemo(() => {
    const aws: Record<string, Record<string, Workspace[]>> = {};
    const azure: Record<string, Record<string, Workspace[]>> = {};
    const gcp: Record<string, Record<string, Workspace[]>> = {};
    for (const w of filtered) {
      const c = classify(w);
      const bucket = c.cloud === "azure" ? azure : c.cloud === "gcp" ? gcp : aws;
      const g = (bucket[c.key] ??= {});
      (g[c.region] ??= []).push(w);
    }
    for (const bucket of [aws, azure, gcp]) {
      for (const g of Object.values(bucket)) {
        for (const region of Object.keys(g)) g[region].sort((a, b) => a.name.localeCompare(b.name));
      }
    }
    return { awsGrouped: aws, azureGrouped: azure, gcpGrouped: gcp };
    // classify closes over the cloud lookup maps, which are themselves memoized.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtered, azureByPk, azureByGuid, gcpByPk, gcpByProjectId]);

  const accountIds = Object.keys(awsGrouped).sort();
  // Azure groups sorted by display name (registered) then bare GUID key.
  const azureKeys = Object.keys(azureGrouped).sort((a, b) => {
    const na = azureByPk.get(a)?.name ?? a;
    const nb = azureByPk.get(b)?.name ?? b;
    return na.localeCompare(nb);
  });
  // GCP groups sorted by display name (registered) then bare project-id key.
  const gcpKeys = Object.keys(gcpGrouped).sort((a, b) => {
    const na = gcpByPk.get(a)?.name ?? a;
    const nb = gcpByPk.get(b)?.name ?? b;
    return na.localeCompare(nb);
  });
  const groupCount = accountIds.length + azureKeys.length + gcpKeys.length;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter by name, account, region, env…"
          className="max-w-sm"
        />
        <div className="flex flex-wrap items-center gap-3 text-xs text-slate-500">
          <div className="inline-flex items-center gap-1">
            <button
              type="button"
              onClick={() =>
                setExpandSignal((s) => ({ version: (s?.version ?? 0) + 1, expand: true }))
              }
              className="rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
              title="Expand every account, region, and folder"
            >
              Expand all
            </button>
            <button
              type="button"
              onClick={() =>
                setExpandSignal((s) => ({ version: (s?.version ?? 0) + 1, expand: false }))
              }
              className="rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
              title="Collapse every account, region, and folder"
            >
              Collapse all
            </button>
          </div>
          <span>
            {filtered.length} of {workspaces.length} workspace{workspaces.length === 1 ? "" : "s"}
            {filter && (
              <button onClick={() => setFilter("")} className="ml-2 underline-offset-2 hover:underline">
                clear
              </button>
            )}
          </span>
        </div>
      </div>

      {groupCount === 0 ? (
        <Card className="px-6 py-10 text-center text-sm text-slate-500">
          No workspaces match the current filter.
        </Card>
      ) : (
        <div className="space-y-4">
          {accountIds.map((accountId) => (
            <AccountGroup
              key={`aws:${accountId}`}
              accountId={accountId}
              accountName={accountNameById.get(accountId)}
              byRegion={awsGrouped[accountId]}
              latestByWs={latestByWs}
              defaultOpen={false}
              onChanged={onChanged}
              expandSignal={expandSignal}
              awsAccounts={awsAccounts}
              azureSubscriptions={azureSubscriptions}
              gcpProjects={gcpProjects}
            />
          ))}
          {azureKeys.map((key) => (
            <AzureSubscriptionGroup
              key={`azure:${key}`}
              sub={azureByPk.get(key)}
              guid={key.startsWith("guid:") ? key.slice("guid:".length) : undefined}
              byRegion={azureGrouped[key]}
              latestByWs={latestByWs}
              defaultOpen={false}
              onChanged={onChanged}
              expandSignal={expandSignal}
              awsAccounts={awsAccounts}
              azureSubscriptions={azureSubscriptions}
              gcpProjects={gcpProjects}
            />
          ))}
          {gcpKeys.map((key) => (
            <GcpProjectGroup
              key={`gcp:${key}`}
              proj={gcpByPk.get(key)}
              projectId={key.startsWith("pid:") ? key.slice("pid:".length) : undefined}
              byRegion={gcpGrouped[key]}
              latestByWs={latestByWs}
              defaultOpen={false}
              onChanged={onChanged}
              expandSignal={expandSignal}
              awsAccounts={awsAccounts}
              azureSubscriptions={azureSubscriptions}
              gcpProjects={gcpProjects}
            />
          ))}
        </div>
      )}
    </div>
  );
}
