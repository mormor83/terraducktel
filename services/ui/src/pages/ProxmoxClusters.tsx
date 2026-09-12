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

type ProxmoxCluster = {
  id: string;
  business_unit_id: string;
  slug: string;
  name: string;
  description?: string | null;
  color?: string | null;
  color_effective?: string;
  endpoint: string;
  api_token_id: string;
  token_secret_masked_tail: string;
  ssh_username?: string | null;
  has_ssh_key: boolean;
  tls_insecure: boolean;
  ca_cert_pem?: string | null;
};

type FormState = {
  slug: string;
  name: string;
  description: string;
  endpoint: string;
  api_token_id: string;
  api_token_secret: string;
  ssh_username: string;
  ssh_private_key: string;
  tls_insecure: boolean;
  ca_cert_pem: string;
  color: string;
};

const EMPTY: FormState = {
  slug: "",
  name: "",
  description: "",
  endpoint: "https://",
  api_token_id: "",
  api_token_secret: "",
  ssh_username: "",
  ssh_private_key: "",
  tls_insecure: false,
  ca_cert_pem: "",
  color: "",
};

const TEXTAREA_CLS =
  "block w-full rounded-md border border-brand-border bg-white px-3 py-2 font-mono text-xs " +
  "text-brand-text placeholder-brand-muted transition-colors focus:border-brand-400 focus:outline-none " +
  "focus:ring-2 focus:ring-brand-400/30 dark:border-slate-700/70 dark:bg-slate-950/60 " +
  "dark:text-slate-100 dark:placeholder-slate-500";

function ClusterForm({
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
        <CardTitle>{editing ? "Edit Proxmox cluster" : "Add Proxmox cluster"}</CardTitle>
      </CardHeader>
      <CardBody>
        <form onSubmit={submit} className="grid gap-3 md:grid-cols-2">
          <div>
            <Label>Name</Label>
            <Input value={f.name} onChange={(e) => update("name", e.target.value)} required />
          </div>
          <div>
            <Label>Slug</Label>
            <Input
              placeholder="home-lab"
              value={f.slug}
              onChange={(e) => update("slug", e.target.value)}
              required
              disabled={editing}
              pattern="[a-z][a-z0-9-]{1,38}[a-z0-9]"
              title="lowercase letters, digits and hyphens; used in repo paths as proxmox/cluster-<slug>/"
            />
          </div>
          <div className="md:col-span-2">
            <Label>API endpoint</Label>
            <Input
              placeholder="https://pve.example.com:8006"
              value={f.endpoint}
              onChange={(e) => update("endpoint", e.target.value)}
              required
            />
          </div>
          <div>
            <Label>API token id</Label>
            <Input
              placeholder="terraform@pve!tdt"
              value={f.api_token_id}
              onChange={(e) => update("api_token_id", e.target.value)}
              required
              autoComplete="off"
            />
          </div>
          <div>
            <Label>
              API token secret{" "}
              {editing && <span className="text-xs text-slate-500">(leave blank to keep current)</span>}
            </Label>
            <Input
              type="password"
              value={f.api_token_secret}
              onChange={(e) => update("api_token_secret", e.target.value)}
              required={!editing}
              autoComplete="new-password"
            />
          </div>
          <div className="md:col-span-2 flex items-center gap-2">
            <input
              id="pmx-tls-insecure"
              type="checkbox"
              checked={f.tls_insecure}
              onChange={(e) => update("tls_insecure", e.target.checked)}
            />
            <label htmlFor="pmx-tls-insecure" className="text-sm text-slate-700 dark:text-slate-200">
              Skip TLS certificate verification (self-signed Proxmox certs)
            </label>
          </div>
          <div className="md:col-span-2">
            <Label>CA certificate PEM (optional)</Label>
            <textarea
              className={TEXTAREA_CLS}
              rows={4}
              placeholder="-----BEGIN CERTIFICATE-----"
              value={f.ca_cert_pem}
              onChange={(e) => update("ca_cert_pem", e.target.value)}
              spellCheck={false}
            />
            <p className="mt-1 text-xs text-slate-500">
              Trusted for the connection test and for both Terraform providers at run time.
            </p>
          </div>
          <div className="md:col-span-2 mt-1 border-t border-slate-200 pt-3 dark:border-slate-700">
            <p className="text-xs font-medium text-slate-600 dark:text-slate-300">
              SSH access (optional, bpg/proxmox only)
            </p>
            <p className="text-[11px] text-slate-500">
              Needed only for resources that upload files or snippets to a node.
            </p>
          </div>
          <div>
            <Label>SSH username</Label>
            <Input value={f.ssh_username} onChange={(e) => update("ssh_username", e.target.value)} autoComplete="off" />
          </div>
          <div>
            <Label>
              SSH private key{" "}
              {editing && <span className="text-xs text-slate-500">(blank = keep; clear username to remove)</span>}
            </Label>
            <textarea
              className={TEXTAREA_CLS}
              rows={3}
              placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
              value={f.ssh_private_key}
              onChange={(e) => update("ssh_private_key", e.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
          </div>
          <div className="md:col-span-2">
            <Label>Description</Label>
            <Input value={f.description} onChange={(e) => update("description", e.target.value)} />
          </div>
          <div className="md:col-span-2">
            <Label>Color</Label>
            <AccountColorPicker value={f.color} onChange={(c) => update("color", c)} />
          </div>
          {error && (
            <p className="md:col-span-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">
              {error}
            </p>
          )}
          <div className="md:col-span-2 mt-2 flex items-center gap-2">
            <Button type="submit" disabled={busy}>{busy ? <Spinner /> : editing ? "Save" : "Add cluster"}</Button>
            <Button type="button" variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}

export default function ProxmoxClusters() {
  const [rows, setRows] = useState<ProxmoxCluster[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [showForm, setShowForm] = useState<null | { mode: "create" | "edit"; row?: ProxmoxCluster }>(null);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, { ok: boolean; detail?: string }>>({});
  const [pendingDelete, setPendingDelete] = useState<ProxmoxCluster | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [actionErr, setActionErr] = useState<string | null>(null);

  async function refresh() {
    setLoading(true);
    setErr(null);
    try {
      const r = await api.get("/v1/proxmox-clusters");
      setRows(r.data);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Failed to load Proxmox clusters");
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
      body.description = body.description || null;
      body.ca_cert_pem = body.ca_cert_pem || (showForm?.mode === "edit" ? "" : null);
      if (!body.color) delete body.color;
      if (showForm?.mode === "create") {
        if (!body.ssh_username) delete body.ssh_username;
        if (!body.ssh_private_key) delete body.ssh_private_key;
        await api.post("/v1/proxmox-clusters", body);
      } else if (showForm?.mode === "edit" && showForm.row) {
        delete body.slug;
        // Blank secret → keep. Blank key with a username → keep. Blank
        // username → the API clears both.
        if (!body.api_token_secret) delete body.api_token_secret;
        if (!body.ssh_private_key) delete body.ssh_private_key;
        if (!body.ssh_username) body.ssh_private_key = "";
        await api.put(`/v1/proxmox-clusters/${showForm.row.id}`, body);
      }
      setShowForm(null);
      await refresh();
    } catch (e: any) {
      const d = e?.response?.data?.detail;
      setFormError(typeof d === "string" ? d : Array.isArray(d) ? d.map((x: any) => x.msg).join("; ") : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    setActionBusy(true);
    setActionErr(null);
    try {
      await api.delete(`/v1/proxmox-clusters/${pendingDelete.id}`);
      setPendingDelete(null);
      await refresh();
    } catch (e: any) {
      setActionErr(e?.response?.data?.detail ?? "Delete failed");
    } finally {
      setActionBusy(false);
    }
  }

  async function onTest(row: ProxmoxCluster) {
    setTestResult((p) => ({ ...p, [row.id]: { ok: false, detail: "Testing…" } }));
    try {
      const r = await api.post(`/v1/proxmox-clusters/${row.id}/test`);
      setTestResult((p) => ({ ...p, [row.id]: r.data }));
    } catch (e: any) {
      setTestResult((p) => ({ ...p, [row.id]: { ok: false, detail: e?.response?.data?.detail ?? "Test failed" } }));
    }
  }

  const initialForm: FormState =
    showForm?.mode === "edit" && showForm.row
      ? {
          slug: showForm.row.slug,
          name: showForm.row.name,
          description: showForm.row.description ?? "",
          endpoint: showForm.row.endpoint,
          api_token_id: showForm.row.api_token_id,
          api_token_secret: "",
          ssh_username: showForm.row.ssh_username ?? "",
          ssh_private_key: "",
          tls_insecure: showForm.row.tls_insecure,
          ca_cert_pem: showForm.row.ca_cert_pem ?? "",
          color: showForm.row.color_effective ?? showForm.row.color ?? "",
        }
      : EMPTY;

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Proxmox clusters</h3>
          <p className="text-xs text-slate-500">
            API tokens used by the <code>bpg/proxmox</code> and <code>Telmate/proxmox</code> providers.
            Workspaces at <code>proxmox/cluster-&lt;slug&gt;/&lt;node&gt;/…</code> link automatically on import.
          </p>
        </div>
        {!showForm && (
          <Button onClick={() => { setFormError(null); setShowForm({ mode: "create" }); }}>+ Add cluster</Button>
        )}
      </div>

      {showForm && (
        <ClusterForm
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
        <EmptyState title="No Proxmox clusters yet" description="Add one to run Terraform against a Proxmox VE cluster." />
      ) : (
        <div className="space-y-3">
          {rows.map((row) => {
            const t = testResult[row.id];
            return (
              <Card key={row.id} className="relative overflow-hidden">
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
                        <Badge tone="info">proxmox</Badge>
                        {row.tls_insecure && <Badge tone="warning">tls verify off</Badge>}
                      </div>
                      <p className="mt-1 font-mono text-[11px] text-slate-500">
                        cluster-{row.slug} · {row.endpoint}
                      </p>
                      <p className="mt-0.5 font-mono text-[11px] text-slate-500">
                        token {row.api_token_id} {row.token_secret_masked_tail}
                      </p>
                      {row.has_ssh_key && (
                        <p className="mt-0.5 font-mono text-[11px] text-slate-500">ssh {row.ssh_username}</p>
                      )}
                      {row.ca_cert_pem && (
                        <p className="mt-0.5 font-mono text-[11px] text-slate-500">custom CA configured</p>
                      )}
                      {row.description && <p className="mt-1 text-xs text-slate-500">{row.description}</p>}
                      {t && (
                        <p className={`mt-2 text-xs ${t.ok ? "text-emerald-700 dark:text-emerald-300" : "text-red-600 dark:text-red-300"}`}>
                          {t.ok ? "✓ " : "✕ "}{t.detail}
                        </p>
                      )}
                    </div>
                    <div className="flex shrink-0 gap-2">
                      <Button size="sm" variant="ghost" onClick={() => onTest(row)}>Test connection</Button>
                      <Button size="sm" variant="ghost" onClick={() => { setFormError(null); setShowForm({ mode: "edit", row }); }}>Edit</Button>
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
        title="Delete Proxmox cluster"
        message={
          <>
            Delete Proxmox cluster <strong>"{pendingDelete?.name}"</strong>? Workspaces linked to
            it will be unlinked and their next run will fail at provider auth.
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
