// The workspace leaf row: the branch/status chip + Run/Destroy/Delete actions,
// and the expandable detail panel (id, region, state-aws-account override,
// azure-subscription link, repo, auto-trigger, last run, state-lock release).

import { useEffect, useState, type ReactNode } from "react";
import { api } from "../../api/client";
import { useCurrentUser, hasMinRole } from "../../hooks/useAuth";
import { Badge, Button, ConfirmDialog, cx, DriftBadge } from "../ui";
import { RunModal } from "../RunModal";
import { FileIcon, HelmChip } from "./icons";
import { azureInfo, gcpInfo } from "./paths";
import { BranchStatusChip, InlineLinkEditor, TreeRow } from "./primitives";
import type {
  AwsAccountLite,
  AzureSubscriptionLite,
  GcpProjectLite,
  Run,
  Workspace,
} from "./types";

// A single label/value row in the workspace-detail metadata table.
function MetaRow({
  label,
  title,
  children,
}: {
  label: string;
  title?: string;
  children: ReactNode;
}) {
  return (
    <tr className="border-t border-slate-100 first:border-t-0 dark:border-slate-800/50">
      <td className="whitespace-nowrap py-1.5 pr-4 align-top text-slate-400" title={title}>
        {label}
      </td>
      <td className="w-full py-1.5 align-top">{children}</td>
    </tr>
  );
}

export function WorkspaceLeafRow({
  workspace,
  displayName,
  depth,
  recentRun,
  onChanged,
  awsAccounts,
  azureSubscriptions,
  gcpProjects,
}: {
  workspace: Workspace;
  displayName: string;
  depth: number;
  recentRun?: Run;
  onChanged: () => void;
  awsAccounts: AwsAccountLite[];
  azureSubscriptions: AzureSubscriptionLite[];
  gcpProjects: GcpProjectLite[];
}) {
  const user = useCurrentUser();
  const [busy, setBusy] = useState<null | string>(null);
  const [err, setErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  // Single unified modal for both Run and Destroy. The command discriminates;
  // the modal handles branch + reviewer + variables in one screen.
  const [runModal, setRunModal] = useState<null | "apply" | "destroy">(null);
  const branch = workspace.repo_ref || "main";
  const webhookEnabled = !!workspace.webhook_enabled;

  // The two credential links (state-aws-account override + azure-subscription)
  // share one `InlineLinkEditor` UI and the `saveLink` handler below.
  //
  // state-aws-account: decoupled from aws_account_id (which controls tree
  // grouping). Points the executor at a different account's per-account creds
  // for the terraform state backend — e.g. a non-AWS workspace whose state
  // lives in an AWS S3 bucket owned by a different account.
  //
  // azure-subscription: setting it is what makes the executor inject ARM_* env
  // vars so the azurerm provider authenticates as the subscription's service
  // principal (instead of falling back to the Azure CLI). Shown only for Azure
  // workspaces — linked, or detected from the `azure/subscription-<guid>/` path.
  const isAzure = !!workspace.azure_subscription_id || !!azureInfo(workspace);
  const linkedAzureSub = azureSubscriptions.find(
    (s) => s.id === workspace.azure_subscription_id,
  );
  // GCP mirror of the Azure link: auto-derived from the gcp/project-<id>/ path
  // or the explicit gcp_project_id link. Read-only in the row.
  const isGcp = !!workspace.gcp_project_id || !!gcpInfo(workspace);
  const linkedGcpProject = gcpProjects.find((p) => p.id === workspace.gcp_project_id);

  // State backend is constrained by which cloud the workspace is linked to: s3
  // always works; azureblob/gcs need a linked Azure subscription / GCP project.
  // When only one backend is possible the picker is "pinned" (the server would
  // reject any other choice anyway) so we gray it out. The current value is
  // always kept selectable so the control never shows a stale/invalid option.
  const currentBackend = workspace.state_backend ?? "s3";
  const validBackends = new Set<string>(["s3", currentBackend]);
  if (isAzure) validBackends.add("azureblob");
  if (isGcp) validBackends.add("gcs");
  const backendPinned = validBackends.size === 1;

  // ─── State-lock status (lazy-fetched on expand) ────────────────────────
  type LockStatus = { held: boolean; run_id?: string | null; acquired_at?: string | null };
  const [lockStatus, setLockStatus] = useState<LockStatus | null>(null);
  const [releaseLockOpen, setReleaseLockOpen] = useState(false);

  // Delete confirmations (in-app dialogs — no native popups).
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [forceDeleteOpen, setForceDeleteOpen] = useState(false);
  // "Untrack": remove a still-in-repo git-synced workspace from TDT without
  // destroying its infra (regret an import). Re-import brings it back.
  const [untrackOpen, setUntrackOpen] = useState(false);
  // Whether a force-delete should also drop the tfstate in S3. Default off:
  // keeping the state lets the workspace be recovered by re-importing.
  const [forceDeleteState, setForceDeleteState] = useState(false);

  async function refreshLockStatus() {
    try {
      const r = await api.get(`/v1/workspaces/${workspace.id}/state-lock`);
      setLockStatus(r.data);
    } catch {
      setLockStatus(null);
    }
  }

  useEffect(() => {
    if (expanded) void refreshLockStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded]);

  async function forceReleaseLock() {
    setReleaseLockOpen(false);
    setErr(null);
    setBusy("force-unlock");
    try {
      await api.delete(`/v1/workspaces/${workspace.id}/state-lock`);
      await refreshLockStatus();
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? "Force unlock failed");
    } finally {
      setBusy(null);
    }
  }

  async function toggleWebhook(next: boolean) {
    setErr(null);
    setBusy("webhook");
    try {
      await api.put(`/v1/workspaces/${workspace.id}`, { webhook_enabled: next });
      onChanged();
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? "Update failed");
    } finally {
      setBusy(null);
    }
  }

  // Rebind one of the workspace's credential links. Empty string clears it
  // (server normalizes to NULL). Returns whether the save succeeded so the
  // InlineLinkEditor knows whether to collapse.
  async function saveLink(
    field: "state_aws_account_id" | "azure_subscription_id" | "state_backend",
    value: string,
    busyKey: string,
  ): Promise<boolean> {
    setErr(null);
    setBusy(busyKey);
    try {
      await api.put(`/v1/workspaces/${workspace.id}`, { [field]: value });
      onChanged();
      return true;
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? "Update failed");
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    setDeleteOpen(false);
    setErr(null);
    setBusy("delete");
    try {
      await api.delete(`/v1/workspaces/${workspace.id}`);
      onChanged();
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? "Delete failed");
    } finally {
      setBusy(null);
    }
  }

  async function forceDeleteOrphan() {
    setForceDeleteOpen(false);
    setErr(null);
    setBusy("delete");
    try {
      await api.delete(
        `/v1/workspaces/${workspace.id}?force=true&delete_state=${forceDeleteState ? "true" : "false"}`,
      );
      onChanged();
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? "Force delete failed");
    } finally {
      setBusy(null);
    }
  }

  // Untrack = force-delete but always keep the tfstate + infra. `force=true`
  // bypasses the git-synced 409; `delete_state=false` leaves S3 state (and the
  // real resources) untouched. The row + its runs/drift/locks are cleared. It
  // won't come back on its own (discovery/import is manual) — re-import to
  // manage it again.
  async function untrack() {
    setUntrackOpen(false);
    setErr(null);
    setBusy("delete");
    try {
      await api.delete(`/v1/workspaces/${workspace.id}?force=true&delete_state=false`);
      onChanged();
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? "Untrack failed");
    } finally {
      setBusy(null);
    }
  }

  const isGitSynced = !!workspace.repo_url && !workspace.repo_url.startsWith("local://");
  const isOrphaned = workspace.path_status === "orphaned";
  // Branch+status chip: color reflects the most recent run state on this
  // workspace (not the workspace itself). Operators pick branches inside the
  // Run modal — clicking the chip is intentionally a no-op, the chip is a
  // status indicator, not an action.
  const chip = <BranchStatusChip branch={branch} run={recentRun} webhookEnabled={webhookEnabled} />;

  const isHelm = workspace.kind === "helm";

  const right = (
    <>
      {chip}
      {isHelm && <HelmChip />}
      {isOrphaned && (
        <span title="The source path was deleted/renamed in the repo. Run/Destroy will fail; use Force delete to remove from TDT.">
          <Badge tone="warning">orphaned · path missing</Badge>
        </span>
      )}
      {workspace.drift_status !== "unknown" && <DriftBadge status={workspace.drift_status} />}
      {hasMinRole(user, "operator") && !isOrphaned && (
        <>
          <Button
            size="sm"
            variant="warning"
            onClick={() => setRunModal("apply")}
            disabled={!!busy}
            title="Pick branch + variables → terraform plan → pause for approval → apply."
          >
            Run
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setRunModal("destroy")}
            disabled={!!busy}
            title="terraform destroy — same flow as Run, but removes all resources from state."
            className="text-red-600 hover:text-red-500"
          >
            Destroy
          </Button>
        </>
      )}
      {hasMinRole(user, "admin") && isOrphaned && isGitSynced && (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            setForceDeleteState(false);
            setForceDeleteOpen(true);
          }}
          disabled={!!busy}
          className="text-red-500 hover:text-red-400"
          title="Force-delete this orphaned workspace. The source path is missing from the repo, so a real destroy isn't possible."
        >
          {busy === "delete" ? "…" : "Force delete"}
        </Button>
      )}
      {hasMinRole(user, "admin") && isGitSynced && !isOrphaned && (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setUntrackOpen(true)}
          disabled={!!busy}
          className="text-amber-600 hover:text-amber-500"
          title="Remove this workspace from Terraducktel WITHOUT destroying its infrastructure — the Terraform module stays in the repo and the real resources + tfstate are untouched. TDT just stops tracking it; re-import to manage it again."
        >
          {busy === "delete" ? "…" : "Untrack"}
        </Button>
      )}
      {hasMinRole(user, "admin") && !isGitSynced && (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setDeleteOpen(true)}
          disabled={!!busy}
          className="text-red-500 hover:text-red-400"
          title="Delete workspace + all its runs, drift reports, and state locks."
        >
          {busy === "delete" ? "…" : "Delete"}
        </Button>
      )}
    </>
  );

  return (
    <>
      <TreeRow
        depth={depth}
        icon={<FileIcon />}
        label={<span className="font-medium">{displayName}</span>}
        meta={workspace.tf_working_dir && workspace.tf_working_dir !== "." ? (
          <span className="font-mono text-[11px]">{workspace.tf_working_dir}</span>
        ) : null}
        right={right}
        onToggle={() => setExpanded((v) => !v)}
        open={expanded}
        className="border-t border-slate-100 dark:border-slate-800/50"
      />
      {(expanded || err) && (
        <div
          style={{ paddingLeft: 16 + depth * 20 + 30 }}
          className="border-t border-slate-100 bg-slate-50/60 px-4 py-2.5 text-xs text-slate-600 dark:border-slate-800/50 dark:bg-slate-900/40 dark:text-slate-400"
        >
          {err && (
            <div className="mb-2 rounded-md border border-red-200 bg-red-50 px-2.5 py-1.5 text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
              {err}
            </div>
          )}
          {expanded && (
            <>
              <table className="w-full border-collapse text-xs">
                <tbody>
                  <MetaRow label="id">
                    <span className="font-mono text-[11px]">{workspace.id}</span>
                  </MetaRow>
                  <MetaRow label="region">
                    <span className="font-mono text-[11px]">{workspace.region}</span>
                  </MetaRow>
                  <MetaRow
                    label="state aws account"
                    title="AWS creds used to open the terraform state backend (S3). Defaults to the workspace's own aws_account_id. Set this when the state lives in a bucket owned by a different account — e.g. a non-AWS (Cloudflare/Azure) workspace whose state happens to be in AWS S3."
                  >
                    <InlineLinkEditor
                      bare
                      label="state aws account"
                      labelTitle="AWS creds used to open the terraform state backend (S3). Defaults to the workspace's own aws_account_id. Set this when the state lives in a bucket owned by a different account — e.g. a non-AWS (Cloudflare/Azure) workspace whose state happens to be in AWS S3."
                      changeTitle="Override which AWS account's creds the executor uses for the terraform state backend. Useful for non-AWS workspaces whose state lives in an AWS S3 bucket owned by another account."
                      emptyLabel="(same as workspace aws account)"
                      options={awsAccounts.map((a) => ({
                        value: a.account_id,
                        label: `${a.account_id} — ${a.name}`,
                      }))}
                      current={workspace.state_aws_account_id ?? ""}
                      display={
                        workspace.state_aws_account_id
                          ? workspace.state_aws_account_id
                          : `(same as workspace aws account: ${workspace.aws_account_id})`
                      }
                      monoSelect
                      canEdit={hasMinRole(user, "admin")}
                      busy={busy === "rebind-state"}
                      onSave={(v) => saveLink("state_aws_account_id", v, "rebind-state")}
                    />
                  </MetaRow>
                  {isAzure && (
                    <MetaRow
                      label="azure subscription"
                      title="Auto-derived from the workspace path (azure/subscription-<id>/…); injects ARM_* service-principal creds."
                    >
                      <span className="font-mono text-[11px]">
                        {linkedAzureSub
                          ? `${linkedAzureSub.name} (${linkedAzureSub.subscription_id})`
                          : workspace.azure_subscription_id
                            ? workspace.azure_subscription_id
                            : "(auto-derived from path)"}
                      </span>
                    </MetaRow>
                  )}
                  {isGcp && (
                    <MetaRow
                      label="gcp project"
                      title="Auto-derived from the workspace path (gcp/project-<id>/…); injects the project's service-account credentials for the google provider."
                    >
                      <span className="font-mono text-[11px]">
                        {linkedGcpProject
                          ? `${linkedGcpProject.name} (${linkedGcpProject.project_id})`
                          : workspace.gcp_project_id
                            ? workspace.gcp_project_id
                            : "(auto-derived from path)"}
                      </span>
                    </MetaRow>
                  )}
                  <MetaRow
                    label="state backend"
                    title="Where this workspace's Terraform state is stored. azureblob/gcs require a linked subscription/project whose storage target is configured — otherwise the save is rejected."
                  >
                    {hasMinRole(user, "admin") ? (
                      <span className="inline-flex items-center gap-1.5">
                        <select
                          value={currentBackend}
                          disabled={busy === "rebind-backend" || backendPinned}
                          onChange={(e) => saveLink("state_backend", e.target.value, "rebind-backend")}
                          title={
                            backendPinned
                              ? "Pinned to s3 — link an Azure subscription or GCP project to enable an alternative state backend."
                              : undefined
                          }
                          className={cx(
                            "rounded border border-brand-border bg-white px-1.5 py-0.5 font-mono text-[11px] text-brand-text dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100",
                            backendPinned && "cursor-not-allowed opacity-60",
                          )}
                        >
                          {["s3", "azureblob", "gcs"].map((b) => (
                            <option key={b} value={b} disabled={!validBackends.has(b)}>
                              {validBackends.has(b) ? b : `${b} · needs linked provider`}
                            </option>
                          ))}
                        </select>
                        {backendPinned && <span className="text-[10px] text-slate-400">pinned</span>}
                      </span>
                    ) : (
                      <span className="font-mono text-[11px]">{currentBackend}</span>
                    )}
                  </MetaRow>
                  {workspace.repo_url && (
                    <MetaRow label="repo">
                      <span className="break-all font-mono text-[11px]">{workspace.repo_url}</span>
                    </MetaRow>
                  )}
                  {isGitSynced && (
                    <MetaRow
                      label="auto-trigger on push"
                      title="When ON, a push to this workspace's tracked branch in the source repo triggers a plan run automatically (provided the push touches files in this workspace's tf_working_dir). Needs the per-BU webhook secret configured in Settings → Webhooks."
                    >
                      {hasMinRole(user, "admin") ? (
                        <label className="inline-flex cursor-pointer items-center gap-1.5 align-middle">
                          <input
                            type="checkbox"
                            checked={webhookEnabled}
                            disabled={busy === "webhook"}
                            onChange={(e) => toggleWebhook(e.target.checked)}
                            className="h-3.5 w-3.5"
                          />
                          <span className="font-mono text-[11px]">
                            {webhookEnabled ? "enabled ⚡" : "disabled"}
                          </span>
                        </label>
                      ) : (
                        <span className="font-mono text-[11px]">
                          {webhookEnabled ? "enabled ⚡" : "disabled"}
                        </span>
                      )}
                    </MetaRow>
                  )}
                  {recentRun && (
                    <MetaRow label="last run">
                      <span className="font-mono text-[11px]">{recentRun.id.slice(0, 8)}</span>{" "}
                      <span>{recentRun.command}</span>
                    </MetaRow>
                  )}
                </tbody>
              </table>
              {lockStatus?.held && (
                <div
                  className="mt-2 flex flex-wrap items-center gap-2 rounded-md border border-amber-300/60 bg-amber-50 px-2.5 py-1.5 text-[11px] text-amber-800 dark:border-amber-700/40 dark:bg-amber-950/30 dark:text-amber-200"
                  title="A terraform state lock is currently held. Releasing it while an executor is still running can let two runs race state — only release when certain no executor is alive."
                >
                  <span>
                    state lock held by run{" "}
                    <span className="font-mono">
                      {(lockStatus.run_id ?? "unknown").slice(0, 8)}
                    </span>
                    {lockStatus.acquired_at && (
                      <> · since {new Date(lockStatus.acquired_at).toLocaleTimeString()}</>
                    )}
                  </span>
                  {hasMinRole(user, "operator") && (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setReleaseLockOpen(true)}
                      disabled={!!busy}
                      className="ml-auto text-red-600 hover:text-red-500"
                    >
                      {busy === "force-unlock" ? "…" : "Release state lock"}
                    </Button>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}
      <ConfirmDialog
        open={releaseLockOpen}
        title="Force-release state lock"
        message={
          <>
            This clears the terraform state lock for <span className="font-mono">{displayName}</span>.
            Only do this if you're certain no executor is currently running against this workspace —
            releasing under a live apply can let a concurrent run race state.
          </>
        }
        confirmLabel="Release lock"
        cancelLabel="Keep lock"
        tone="danger"
        busy={busy === "force-unlock"}
        onConfirm={forceReleaseLock}
        onCancel={() => setReleaseLockOpen(false)}
      />
      <ConfirmDialog
        open={deleteOpen}
        title="Delete workspace"
        message={
          <>
            Delete workspace <span className="font-mono">{workspace.name}</span>? All runs, drift
            reports, and state locks for this workspace will be removed.
          </>
        }
        confirmLabel="Delete"
        tone="danger"
        busy={busy === "delete"}
        onConfirm={remove}
        onCancel={() => setDeleteOpen(false)}
      />
      <ConfirmDialog
        open={forceDeleteOpen}
        title="Force-delete orphaned workspace"
        message={
          <div className="space-y-2.5">
            <p>
              Force-delete orphaned workspace <span className="font-mono">{workspace.name}</span>?
              The source path is missing from the repo, so a real terraform destroy isn't possible.
              This removes the TDT row + all runs/drift reports/state locks.
            </p>
            <label className="flex cursor-pointer items-start gap-2">
              <input
                type="checkbox"
                checked={forceDeleteState}
                onChange={(e) => setForceDeleteState(e.target.checked)}
                disabled={busy === "delete"}
                className="mt-0.5 h-3.5 w-3.5"
              />
              <span>
                Also delete the tfstate file in S3. You won't be able to recover this workspace by
                re-importing. Leave unchecked to keep the tfstate (recommended).
              </span>
            </label>
          </div>
        }
        confirmLabel={forceDeleteState ? "Force delete + tfstate" : "Force delete"}
        tone="danger"
        busy={busy === "delete"}
        onConfirm={forceDeleteOrphan}
        onCancel={() => setForceDeleteOpen(false)}
      />
      <ConfirmDialog
        open={untrackOpen}
        title="Untrack workspace"
        message={
          <>
            Remove <span className="font-mono">{workspace.name}</span> from Terraducktel? This does{" "}
            <b>not</b> run terraform destroy — the Terraform module stays in the repo and the real
            infrastructure + tfstate are left untouched. TDT just stops tracking it (its runs, drift
            reports, and state locks are cleared). It won't reappear on its own; re-import the path
            to manage it again.
          </>
        }
        confirmLabel="Untrack"
        cancelLabel="Cancel"
        tone="warning"
        busy={busy === "delete"}
        onConfirm={untrack}
        onCancel={() => setUntrackOpen(false)}
      />
      {runModal !== null && (
        <RunModal
          workspaceId={workspace.id}
          workspaceName={displayName}
          currentBranch={branch}
          command={runModal}
          onClose={() => setRunModal(null)}
          onDone={() => onChanged()}
        />
      )}
    </>
  );
}
