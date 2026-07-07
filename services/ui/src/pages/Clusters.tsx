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
  SectionHeader,
  Skeleton,
  Spinner,
} from "../components/ui";

type Cluster = {
  id: string;
  business_unit_id: string;
  name: string;
  description?: string | null;
  server_url?: string | null;
  default_namespace?: string | null;
  aws_account_id?: string | null;
  kubeconfig_tail: string;
  created_at?: string | null;
};

type FormState = {
  name: string;
  description: string;
  server_url: string;
  default_namespace: string;
  aws_account_id: string;
  kubeconfig: string;
};

const EMPTY: FormState = {
  name: "",
  description: "",
  server_url: "",
  default_namespace: "default",
  aws_account_id: "",
  kubeconfig: "",
};

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
        <CardTitle>{editing ? "Edit cluster" : "Add cluster"}</CardTitle>
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
          Close
        </Button>
      </CardHeader>
      <CardBody>
        <form onSubmit={submit} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label>Display name</Label>
              <Input
                value={f.name}
                onChange={(e) => update("name", e.target.value)}
                placeholder="prod-eks"
                required
              />
            </div>
            <div>
              <Label>Default namespace</Label>
              <Input
                value={f.default_namespace}
                onChange={(e) => update("default_namespace", e.target.value)}
                placeholder="default"
              />
            </div>
            <div className="sm:col-span-2">
              <Label>Description</Label>
              <Input
                value={f.description}
                onChange={(e) => update("description", e.target.value)}
                placeholder="Production EKS cluster (eu-west-1)"
              />
            </div>
            <div className="sm:col-span-2">
              <Label>Server URL (optional)</Label>
              <Input
                value={f.server_url}
                onChange={(e) => update("server_url", e.target.value)}
                placeholder="https://ABCDEF.gr7.eu-west-1.eks.amazonaws.com"
              />
              <p className="mt-1 text-xs text-slate-500">
                Informational only — the API server endpoint is read from the kubeconfig at run
                time. Set it here for display if you like.
              </p>
            </div>
            <div className="sm:col-span-2">
              <Label>AWS account for EKS auth (optional)</Label>
              <Input
                value={f.aws_account_id}
                onChange={(e) => update("aws_account_id", e.target.value)}
                placeholder="222222222222"
              />
              <p className="mt-1 text-xs text-slate-500">
                EKS kubeconfigs authenticate via <code className="font-mono">aws eks get-token</code>.
                Set the 12-digit AWS account id (must be onboarded in Cloud Providers) whose
                credentials should mint the token for Test connection and helm runs. Leave blank for
                clusters whose kubeconfig carries a static token/cert.
              </p>
            </div>
            <div className="sm:col-span-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-300">
              <strong>Kubeconfig is encrypted at rest</strong> with the same Fernet/HKDF scheme
              used for AWS credentials. The plaintext you paste here is sent over HTTPS, encrypted
              on the API server, and never returned in any subsequent API response.{" "}
              {editing && "Leave blank to keep the existing kubeconfig."}
            </div>
            <div className="sm:col-span-2">
              <Label>Kubeconfig (YAML)</Label>
              <textarea
                value={f.kubeconfig}
                onChange={(e) => update("kubeconfig", e.target.value)}
                autoComplete="off"
                spellCheck={false}
                required={!editing}
                placeholder={
                  editing
                    ? "(unchanged — paste a new kubeconfig to replace it)"
                    : "apiVersion: v1\nkind: Config\nclusters:\n  - cluster:\n      server: https://…\n…"
                }
                rows={10}
                className="block w-full rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-xs text-slate-800 shadow-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200"
              />
            </div>
          </div>
          {error && (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
              {error}
            </div>
          )}
          <div className="flex gap-2">
            <Button type="submit" disabled={busy}>
              {busy ? (
                <>
                  <Spinner /> Saving…
                </>
              ) : editing ? (
                "Save changes"
              ) : (
                "Add cluster"
              )}
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

export default function Clusters() {
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [showForm, setShowForm] = useState<null | { mode: "create" | "edit"; row?: Cluster }>(null);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<
    Record<string, { ok: boolean; detail?: string; context?: string }>
  >({});
  // Cluster pending delete confirmation (in-app dialog, no native popup).
  const [pendingDelete, setPendingDelete] = useState<Cluster | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const r = await api.get("/v1/clusters");
      setClusters(r.data);
      setErr(null);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? "Failed to load");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function onCreate(f: FormState) {
    setBusy(true);
    setFormError(null);
    try {
      await api.post("/v1/clusters", {
        name: f.name,
        description: f.description || undefined,
        server_url: f.server_url || undefined,
        default_namespace: f.default_namespace || undefined,
        aws_account_id: f.aws_account_id.trim() || undefined,
        kubeconfig: f.kubeconfig,
      });
      setShowForm(null);
      await load();
    } catch (e: any) {
      setFormError(e?.response?.data?.detail ?? e?.message ?? "Create failed");
    } finally {
      setBusy(false);
    }
  }

  async function onEdit(f: FormState) {
    if (!showForm?.row) return;
    setBusy(true);
    setFormError(null);
    try {
      const body: any = {
        name: f.name,
        description: f.description || null,
        server_url: f.server_url || null,
        default_namespace: f.default_namespace || null,
        aws_account_id: f.aws_account_id.trim() || null,
      };
      // Only send kubeconfig when the operator entered a new one; blank keeps
      // the existing encrypted value untouched.
      if (f.kubeconfig) body.kubeconfig = f.kubeconfig;
      await api.put(`/v1/clusters/${showForm.row.id}`, body);
      setShowForm(null);
      await load();
    } catch (e: any) {
      setFormError(e?.response?.data?.detail ?? e?.message ?? "Save failed");
    } finally {
      setBusy(false);
    }
  }

  async function onDelete() {
    if (!pendingDelete) return;
    setDeleteBusy(true);
    setErr(null);
    try {
      await api.delete(`/v1/clusters/${pendingDelete.id}`);
      setPendingDelete(null);
      await load();
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? "Delete failed");
    } finally {
      setDeleteBusy(false);
    }
  }

  async function onTest(row: Cluster) {
    setTestResult((p) => ({ ...p, [row.id]: { ok: false, detail: "Testing…" } }));
    try {
      const r = await api.post(`/v1/clusters/${row.id}/test`);
      setTestResult((p) => ({ ...p, [row.id]: r.data }));
    } catch (e: any) {
      setTestResult((p) => ({
        ...p,
        [row.id]: { ok: false, detail: e?.response?.data?.detail ?? e?.message ?? "Test failed" },
      }));
    }
  }

  return (
    <div>
      <SectionHeader
        title="Kubernetes Clusters"
        subtitle="Clusters host Helm-kind workspaces. The kubeconfig is encrypted at rest and never returned in plaintext."
        action={
          !showForm && <Button onClick={() => setShowForm({ mode: "create" })}>+ Add cluster</Button>
        }
      />

      {showForm && (
        <ClusterForm
          initial={
            showForm.row
              ? {
                  name: showForm.row.name,
                  description: showForm.row.description ?? "",
                  server_url: showForm.row.server_url ?? "",
                  default_namespace: showForm.row.default_namespace ?? "",
                  aws_account_id: showForm.row.aws_account_id ?? "",
                  kubeconfig: "",
                }
              : EMPTY
          }
          editing={showForm.mode === "edit"}
          onSubmit={showForm.mode === "edit" ? onEdit : onCreate}
          onCancel={() => {
            setShowForm(null);
            setFormError(null);
          }}
          busy={busy}
          error={formError}
        />
      )}

      {err && (
        <Card className="mb-4 border-red-900/40 bg-red-950/30">
          <CardBody className="text-sm text-red-300">{err}</CardBody>
        </Card>
      )}

      {loading ? (
        <Card>
          <CardBody className="space-y-3">
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-4 w-2/3" />
          </CardBody>
        </Card>
      ) : clusters.length === 0 ? (
        <EmptyState
          title="No clusters configured yet"
          description="Add a cluster to host Helm-kind workspaces. Each cluster stores an encrypted kubeconfig the executor uses for helm upgrade/diff/uninstall."
          action={<Button onClick={() => setShowForm({ mode: "create" })}>+ Add cluster</Button>}
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {clusters.map((c) => {
            const tr = testResult[c.id];
            return (
              <Card key={c.id}>
                <CardHeader>
                  <div className="flex min-w-0 items-center gap-3">
                    <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-blue-100 text-sm font-semibold text-blue-700 dark:bg-blue-900/40 dark:text-blue-300">
                      k8s
                    </span>
                    <div className="min-w-0">
                      <CardTitle className="truncate">{c.name}</CardTitle>
                      {c.server_url && (
                        <p className="truncate font-mono text-xs text-slate-500">{c.server_url}</p>
                      )}
                    </div>
                  </div>
                  {c.default_namespace && <Badge tone="info">{c.default_namespace}</Badge>}
                </CardHeader>
                <CardBody className="space-y-3">
                  {c.description && (
                    <p className="text-sm text-slate-500 dark:text-slate-400">{c.description}</p>
                  )}
                  <div className="grid grid-cols-2 gap-2 text-sm">
                    <div className="col-span-2">
                      <p className="text-xs uppercase tracking-wider text-slate-500">Kubeconfig</p>
                      <p className="font-mono text-slate-700 dark:text-slate-300">
                        …{c.kubeconfig_tail}
                      </p>
                    </div>
                  </div>
                  {tr && (
                    <div
                      className={
                        "rounded-md border px-3 py-2 text-xs " +
                        (tr.ok
                          ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-300"
                          : "border-red-200 bg-red-50 text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300")
                      }
                    >
                      {tr.ok ? (
                        <p>
                          ✓ Reachable
                          {tr.context && (
                            <span>
                              {" "}
                              · context <span className="font-mono">{tr.context}</span>
                            </span>
                          )}
                          {tr.detail && <span> — {tr.detail}</span>}
                        </p>
                      ) : (
                        <p>✕ {tr.detail ?? "Connection test failed"}</p>
                      )}
                    </div>
                  )}
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" variant="secondary" onClick={() => onTest(c)}>
                      Test connection
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setShowForm({ mode: "edit", row: c })}>
                      Edit
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="ml-auto text-red-500 hover:text-red-400"
                      onClick={() => setPendingDelete(c)}
                    >
                      Delete
                    </Button>
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
        title="Delete cluster"
        message={
          <>
            Delete cluster <strong>{pendingDelete?.name}</strong>? Helm workspaces tied to this
            cluster will be unable to plan/apply until reconfigured.
          </>
        }
        confirmLabel="Delete"
        busy={deleteBusy}
        onConfirm={onDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}
