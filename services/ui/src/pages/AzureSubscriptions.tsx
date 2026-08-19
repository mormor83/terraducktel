import { FormEvent, useEffect, useState } from "react";
import { api } from "../api/client";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  ConfirmDialog,
  EmptyState,
  Input,
  Label,
  Skeleton,
  Spinner,
} from "../components/ui";
import { AccountColorPicker } from "../components/AccountTag";
import { ACCOUNT_COLOR_CLASSES, asAccountColor } from "../components/accountColors";

type AzureSubscription = {
  id: string;
  business_unit_id: string;
  subscription_id: string;
  tenant_id: string;
  client_id: string;
  name: string;
  description?: string | null;
  default_location: string;
  state_storage_account?: string | null;
  state_container?: string | null;
  // Stored choice (null = auto) vs. what to paint. See accountColors.ts.
  color?: string | null;
  color_effective?: string;
  client_secret_masked: string;
};

type FormState = {
  subscription_id: string;
  tenant_id: string;
  client_id: string;
  client_secret: string;
  name: string;
  description: string;
  default_location: string;
  state_storage_account: string;
  state_container: string;
  color: string;
};

const EMPTY: FormState = {
  subscription_id: "",
  tenant_id: "",
  client_id: "",
  client_secret: "",
  name: "",
  description: "",
  default_location: "eastus",
  state_storage_account: "",
  state_container: "",
  color: "",
};

function SubForm({
  initial,
  editing,
  onSubmit,
  onCancel,
  busy,
  error,
}: {
  initial: FormState;
  editing: boolean;
  onSubmit: (f: FormState) => void;
  onCancel: () => void;
  busy: boolean;
  error: string | null;
}) {
  const [f, setF] = useState<FormState>(initial);
  function update<K extends keyof FormState>(k: K, v: FormState[K]) {
    setF((p) => ({ ...p, [k]: v }));
  }
  function submit(e: FormEvent) {
    e.preventDefault();
    onSubmit(f);
  }
  return (
    <Card className="mb-6">
      <CardHeader>
        <CardTitle>{editing ? "Edit Azure subscription" : "Add Azure subscription"}</CardTitle>
      </CardHeader>
      <CardBody>
        <form onSubmit={submit} className="grid gap-3 md:grid-cols-2">
          <div className="md:col-span-2">
            <Label>Name</Label>
            <Input value={f.name} onChange={(e) => update("name", e.target.value)} required />
          </div>
          <div>
            <Label>Subscription ID</Label>
            <Input
              placeholder="00000000-0000-0000-0000-000000000000"
              value={f.subscription_id}
              onChange={(e) => update("subscription_id", e.target.value)}
              required
              disabled={editing}
            />
          </div>
          <div>
            <Label>Tenant ID</Label>
            <Input
              placeholder="00000000-0000-0000-0000-000000000000"
              value={f.tenant_id}
              onChange={(e) => update("tenant_id", e.target.value)}
              required
              disabled={editing}
            />
          </div>
          <div>
            <Label>Client (application) ID</Label>
            <Input
              placeholder="00000000-0000-0000-0000-000000000000"
              value={f.client_id}
              onChange={(e) => update("client_id", e.target.value)}
              required
              disabled={editing}
            />
          </div>
          <div>
            <Label>Default location</Label>
            <Input value={f.default_location} onChange={(e) => update("default_location", e.target.value)} />
          </div>
          <div className="md:col-span-2">
            <Label>Client secret {editing && <span className="text-xs text-slate-500">(leave blank to keep current)</span>}</Label>
            <Input
              type="password"
              value={f.client_secret}
              onChange={(e) => update("client_secret", e.target.value)}
              required={!editing}
              autoComplete="new-password"
            />
          </div>
          <div className="md:col-span-2">
            <Label>Description</Label>
            <Input value={f.description} onChange={(e) => update("description", e.target.value)} />
          </div>
          <div className="md:col-span-2">
            <Label>Color</Label>
            <AccountColorPicker value={f.color} onChange={(c) => update("color", c)} />
            <p className="mt-1 text-xs text-slate-500">
              Tags this account across the Runs page and Slack alerts. Leave
              untouched on create to get the first color unused in this business unit.
            </p>
          </div>
          <div className="md:col-span-2 mt-1 border-t border-slate-200 pt-3 dark:border-slate-700">
            <p className="text-xs font-medium text-slate-600 dark:text-slate-300">
              Azure Blob state backend (optional)
            </p>
            <p className="text-[11px] text-slate-500">
              Set both to let workspaces store Terraform state in Blob using this
              SP (grant it “Storage Blob Data Contributor”). Leave blank to keep state in S3.
            </p>
          </div>
          <div>
            <Label>Storage account</Label>
            <Input
              placeholder="acmetfstate"
              value={f.state_storage_account}
              onChange={(e) => update("state_storage_account", e.target.value)}
            />
          </div>
          <div>
            <Label>Container</Label>
            <Input
              placeholder="tfstate"
              value={f.state_container}
              onChange={(e) => update("state_container", e.target.value)}
            />
          </div>
          {error && (
            <p className="md:col-span-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">
              {error}
            </p>
          )}
          <div className="md:col-span-2 mt-2 flex items-center gap-2">
            <Button type="submit" disabled={busy}>{busy ? <Spinner /> : editing ? "Save" : "Add subscription"}</Button>
            <Button type="button" variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}

export default function AzureSubscriptions() {
  const [rows, setRows] = useState<AzureSubscription[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [showForm, setShowForm] = useState<null | { mode: "create" | "edit"; row?: AzureSubscription }>(null);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, { ok: boolean; detail?: string }>>({});
  // Subscription pending deletion, awaiting in-app confirmation (no native popups).
  const [pendingDelete, setPendingDelete] = useState<AzureSubscription | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [actionErr, setActionErr] = useState<string | null>(null);

  async function refresh() {
    setLoading(true);
    setErr(null);
    try {
      const r = await api.get("/v1/azure-subscriptions");
      setRows(r.data);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Failed to load Azure subscriptions");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void refresh(); }, []);

  async function onSubmit(f: FormState) {
    setBusy(true);
    setFormError(null);
    try {
      if (showForm?.mode === "create") {
        // Empty Blob-state fields must be omitted — the create schema pattern
        // rejects "" (they're optional, so absent = null).
        const body: any = { ...f };
        if (!body.state_storage_account) delete body.state_storage_account;
        if (!body.state_container) delete body.state_container;
        // Absent (not "") lets the API claim the first free colour.
        if (!body.color) delete body.color;
        await api.post("/v1/azure-subscriptions", body);
      } else if (showForm?.mode === "edit" && showForm.row) {
        // Skip empty client_secret on edit so we keep the existing one.
        const body: any = { ...f };
        if (!body.client_secret) delete body.client_secret;
        // Empty string clears the Blob-state config (send null).
        body.state_storage_account = body.state_storage_account || null;
        body.state_container = body.state_container || null;
        await api.put(`/v1/azure-subscriptions/${showForm.row.id}`, body);
      }
      setShowForm(null);
      await refresh();
    } catch (e: any) {
      setFormError(e?.response?.data?.detail ?? "Save failed");
    } finally {
      setBusy(false);
    }
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    setActionBusy(true);
    setActionErr(null);
    try {
      await api.delete(`/v1/azure-subscriptions/${pendingDelete.id}`);
      setPendingDelete(null);
      await refresh();
    } catch (e: any) {
      setActionErr(e?.response?.data?.detail ?? "Delete failed");
    } finally {
      setActionBusy(false);
    }
  }

  async function onTest(row: AzureSubscription) {
    setTestResult((p) => ({ ...p, [row.id]: { ok: false, detail: "Testing…" } }));
    try {
      const r = await api.post(`/v1/azure-subscriptions/${row.id}/test`);
      setTestResult((p) => ({ ...p, [row.id]: r.data }));
    } catch (e: any) {
      setTestResult((p) => ({ ...p, [row.id]: { ok: false, detail: e?.response?.data?.detail ?? "Test failed" } }));
    }
  }

  function startEdit(row: AzureSubscription) {
    setFormError(null);
    setShowForm({
      mode: "edit",
      row,
    });
  }

  const initialForm: FormState =
    showForm?.mode === "edit" && showForm.row
      ? {
          subscription_id: showForm.row.subscription_id,
          tenant_id: showForm.row.tenant_id,
          client_id: showForm.row.client_id,
          client_secret: "",
          name: showForm.row.name,
          description: showForm.row.description ?? "",
          default_location: showForm.row.default_location,
          state_storage_account: showForm.row.state_storage_account ?? "",
          state_container: showForm.row.state_container ?? "",
          // Prefill with the painted colour so the picker matches Runs.
          color: showForm.row.color_effective ?? showForm.row.color ?? "",
        }
      : EMPTY;

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Azure subscriptions</h3>
          <p className="text-xs text-slate-500">
            Service-principal credentials used by the <code>azurerm</code> provider.
            Optionally set a Blob storage account + container below to store Terraform
            state in Azure Blob instead of S3 (per-workspace, via the state-backend selector).
          </p>
        </div>
        {!showForm && (
          <Button onClick={() => { setFormError(null); setShowForm({ mode: "create" }); }}>+ Add subscription</Button>
        )}
      </div>

      {showForm && (
        <SubForm
          key={showForm.row?.id ?? "create"}
          initial={initialForm}
          editing={showForm.mode === "edit"}
          onSubmit={onSubmit}
          onCancel={() => setShowForm(null)}
          busy={busy}
          error={formError}
        />
      )}

      {actionErr && (
        <p className="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">
          {actionErr}
        </p>
      )}

      {loading ? (
        <Skeleton className="h-24 w-full" />
      ) : err ? (
        <Card><CardBody className="text-sm text-red-600 dark:text-red-300">{err}</CardBody></Card>
      ) : rows.length === 0 ? (
        <EmptyState title="No Azure subscriptions yet" description="Add one to onboard an Azure subscription for terraform runs." />
      ) : (
        <div className="space-y-3">
          {rows.map((row) => {
            const t = testResult[row.id];
            return (
              <Card key={row.id} className="relative overflow-hidden">
                {/* Same rail as a Runs row — see AwsAccounts. */}
                <span
                  aria-hidden
                  className={
                    "absolute inset-y-0 left-0 w-[3px] " +
                    ACCOUNT_COLOR_CLASSES[asAccountColor(row.color_effective ?? row.color)].solid
                  }
                />
                <CardBody>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-semibold text-slate-900 dark:text-slate-100">{row.name}</span>
                        <Badge tone="info">azure</Badge>
                      </div>
                      <p className="mt-1 font-mono text-[11px] text-slate-500">
                        sub {row.subscription_id} · tenant {row.tenant_id}
                      </p>
                      <p className="mt-0.5 font-mono text-[11px] text-slate-500">
                        client {row.client_id} · secret {row.client_secret_masked}
                      </p>
                      {row.state_storage_account && row.state_container && (
                        <p className="mt-0.5 font-mono text-[11px] text-slate-500">
                          blob state {row.state_storage_account}/{row.state_container}
                        </p>
                      )}
                      {row.description && <p className="mt-1 text-xs text-slate-500">{row.description}</p>}
                      {t && (
                        <p className={`mt-2 text-xs ${t.ok ? "text-emerald-700 dark:text-emerald-300" : "text-red-600 dark:text-red-300"}`}>
                          {t.ok ? "✓ " : "✕ "}{t.detail}
                        </p>
                      )}
                    </div>
                    <div className="flex shrink-0 gap-2">
                      <Button size="sm" variant="ghost" onClick={() => onTest(row)}>Test creds</Button>
                      <Button size="sm" variant="ghost" onClick={() => startEdit(row)}>Edit</Button>
                      <Button size="sm" variant="warning" onClick={() => { setActionErr(null); setPendingDelete(row); }}>Delete</Button>
                    </div>
                  </div>
                </CardBody>
              </Card>
            );
          })}
        </div>
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        tone="danger"
        title="Delete Azure subscription"
        message={
          <>
            Delete Azure subscription <strong>"{pendingDelete?.name}"</strong>? Workspaces linked to
            it will be unlinked.
          </>
        }
        confirmLabel="Delete"
        busy={actionBusy}
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}
