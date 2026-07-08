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

type GcpProject = {
  id: string;
  business_unit_id: string;
  project_id: string;
  client_email: string;
  name: string;
  description?: string | null;
  default_region: string;
  state_bucket?: string | null;
  state_prefix?: string | null;
  service_account_masked: string;
};

type FormState = {
  project_id: string;
  name: string;
  description: string;
  default_region: string;
  state_bucket: string;
  state_prefix: string;
  service_account_json: string;
};

const EMPTY: FormState = {
  project_id: "",
  name: "",
  description: "",
  default_region: "us-central1",
  state_bucket: "",
  state_prefix: "",
  service_account_json: "",
};

const TEXTAREA_CLS =
  "block w-full rounded-md border border-brand-border bg-white px-3 py-2 font-mono text-xs " +
  "text-brand-text placeholder-brand-muted transition-colors focus:border-brand-400 focus:outline-none " +
  "focus:ring-2 focus:ring-brand-400/30 dark:border-slate-700/70 dark:bg-slate-950/60 " +
  "dark:text-slate-100 dark:placeholder-slate-500";

function ProjForm({
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
        <CardTitle>{editing ? "Edit GCP project" : "Add GCP project"}</CardTitle>
      </CardHeader>
      <CardBody>
        <form onSubmit={submit} className="grid gap-3 md:grid-cols-2">
          <div className="md:col-span-2">
            <Label>Name</Label>
            <Input value={f.name} onChange={(e) => update("name", e.target.value)} required />
          </div>
          <div>
            <Label>Project ID</Label>
            <Input
              placeholder="acme-prod-1234"
              value={f.project_id}
              onChange={(e) => update("project_id", e.target.value)}
              required
              disabled={editing}
            />
          </div>
          <div>
            <Label>Default region</Label>
            <Input value={f.default_region} onChange={(e) => update("default_region", e.target.value)} />
          </div>
          <div className="md:col-span-2">
            <Label>
              Service-account key JSON{" "}
              {editing && <span className="text-xs text-slate-500">(leave blank to keep current)</span>}
            </Label>
            <textarea
              className={TEXTAREA_CLS}
              rows={6}
              placeholder='{ "type": "service_account", "project_id": "…", "private_key": "…", "client_email": "…" }'
              value={f.service_account_json}
              onChange={(e) => update("service_account_json", e.target.value)}
              required={!editing}
              autoComplete="off"
              spellCheck={false}
            />
          </div>
          <div className="md:col-span-2">
            <Label>Description</Label>
            <Input value={f.description} onChange={(e) => update("description", e.target.value)} />
          </div>
          <div className="md:col-span-2 mt-1 border-t border-slate-200 pt-3 dark:border-slate-700">
            <p className="text-xs font-medium text-slate-600 dark:text-slate-300">
              GCS state backend (optional)
            </p>
            <p className="text-[11px] text-slate-500">
              Set a bucket to let workspaces store Terraform state in GCS using this
              project's service account. Leave blank to keep state in S3.
            </p>
          </div>
          <div>
            <Label>State bucket</Label>
            <Input
              placeholder="acme-prod-tfstate"
              value={f.state_bucket}
              onChange={(e) => update("state_bucket", e.target.value)}
            />
          </div>
          <div>
            <Label>State prefix (optional)</Label>
            <Input value={f.state_prefix} onChange={(e) => update("state_prefix", e.target.value)} />
          </div>
          {error && (
            <p className="md:col-span-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">
              {error}
            </p>
          )}
          <div className="md:col-span-2 mt-2 flex items-center gap-2">
            <Button type="submit" disabled={busy}>{busy ? <Spinner /> : editing ? "Save" : "Add project"}</Button>
            <Button type="button" variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}

export default function GcpProjects() {
  const [rows, setRows] = useState<GcpProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [showForm, setShowForm] = useState<null | { mode: "create" | "edit"; row?: GcpProject }>(null);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, { ok: boolean; detail?: string }>>({});
  const [pendingDelete, setPendingDelete] = useState<GcpProject | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [actionErr, setActionErr] = useState<string | null>(null);

  async function refresh() {
    setLoading(true);
    setErr(null);
    try {
      const r = await api.get("/v1/gcp-projects");
      setRows(r.data);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Failed to load GCP projects");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void refresh(); }, []);

  async function onSubmit(f: FormState) {
    setBusy(true);
    setFormError(null);
    try {
      const body: any = { ...f };
      // Empty optional fields → null (avoid storing "").
      body.state_bucket = body.state_bucket || null;
      body.state_prefix = body.state_prefix || null;
      if (showForm?.mode === "create") {
        await api.post("/v1/gcp-projects", body);
      } else if (showForm?.mode === "edit" && showForm.row) {
        // Blank SA key on edit → keep the existing one.
        if (!body.service_account_json) delete body.service_account_json;
        await api.put(`/v1/gcp-projects/${showForm.row.id}`, body);
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
      await api.delete(`/v1/gcp-projects/${pendingDelete.id}`);
      setPendingDelete(null);
      await refresh();
    } catch (e: any) {
      setActionErr(e?.response?.data?.detail ?? "Delete failed");
    } finally {
      setActionBusy(false);
    }
  }

  async function onTest(row: GcpProject) {
    setTestResult((p) => ({ ...p, [row.id]: { ok: false, detail: "Testing…" } }));
    try {
      const r = await api.post(`/v1/gcp-projects/${row.id}/test`);
      setTestResult((p) => ({ ...p, [row.id]: r.data }));
    } catch (e: any) {
      setTestResult((p) => ({ ...p, [row.id]: { ok: false, detail: e?.response?.data?.detail ?? "Test failed" } }));
    }
  }

  async function onCreateBucket(row: GcpProject) {
    setTestResult((p) => ({ ...p, [row.id]: { ok: false, detail: "Creating bucket…" } }));
    try {
      const r = await api.post(`/v1/gcp-projects/${row.id}/bucket`);
      setTestResult((p) => ({ ...p, [row.id]: r.data }));
    } catch (e: any) {
      setTestResult((p) => ({ ...p, [row.id]: { ok: false, detail: e?.response?.data?.detail ?? "Bucket create failed" } }));
    }
  }

  function startEdit(row: GcpProject) {
    setFormError(null);
    setShowForm({ mode: "edit", row });
  }

  const initialForm: FormState =
    showForm?.mode === "edit" && showForm.row
      ? {
          project_id: showForm.row.project_id,
          name: showForm.row.name,
          description: showForm.row.description ?? "",
          default_region: showForm.row.default_region,
          state_bucket: showForm.row.state_bucket ?? "",
          state_prefix: showForm.row.state_prefix ?? "",
          service_account_json: "",
        }
      : EMPTY;

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">GCP projects</h3>
          <p className="text-xs text-slate-500">
            Service-account keys used by the <code>google</code> provider. Optionally set a
            GCS bucket to store Terraform state in GCS (per-workspace, via the state-backend selector).
          </p>
        </div>
        {!showForm && (
          <Button onClick={() => { setFormError(null); setShowForm({ mode: "create" }); }}>+ Add project</Button>
        )}
      </div>

      {showForm && (
        <ProjForm
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
        <EmptyState title="No GCP projects yet" description="Add one to onboard a GCP project for terraform runs." />
      ) : (
        <div className="space-y-3">
          {rows.map((row) => {
            const t = testResult[row.id];
            return (
              <Card key={row.id}>
                <CardBody>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-semibold text-slate-900 dark:text-slate-100">{row.name}</span>
                        <Badge tone="info">gcp</Badge>
                      </div>
                      <p className="mt-1 font-mono text-[11px] text-slate-500">
                        project {row.project_id} · region {row.default_region}
                      </p>
                      <p className="mt-0.5 font-mono text-[11px] text-slate-500">
                        sa {row.service_account_masked}
                      </p>
                      {row.state_bucket && (
                        <p className="mt-0.5 font-mono text-[11px] text-slate-500">
                          gcs state {row.state_bucket}{row.state_prefix ? `/${row.state_prefix}` : ""}
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
                      {row.state_bucket && (
                        <Button size="sm" variant="ghost" onClick={() => onCreateBucket(row)}>Create bucket</Button>
                      )}
                      <Button size="sm" variant="ghost" onClick={() => startEdit(row)}>Edit</Button>
                      <Button size="sm" variant="danger" onClick={() => { setActionErr(null); setPendingDelete(row); }}>Delete</Button>
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
        title="Delete GCP project"
        message={
          <>
            Delete GCP project <strong>"{pendingDelete?.name}"</strong>? Workspaces linked to
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
