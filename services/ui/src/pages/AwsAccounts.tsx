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

type AwsAccount = {
  id: string;
  account_id: string;
  name: string;
  description?: string | null;
  state_bucket: string;
  state_bucket_region: string;
  default_region: string;
  aws_profile_name?: string | null;
  access_key_id_masked: string;
};

type FormState = {
  account_id: string;
  name: string;
  description: string;
  state_bucket: string;
  state_bucket_region: string;
  default_region: string;
  aws_profile_name: string;
  access_key_id: string;
  secret_access_key: string;
};

const EMPTY: FormState = {
  account_id: "",
  name: "",
  description: "",
  state_bucket: "",
  state_bucket_region: "us-east-1",
  default_region: "us-east-1",
  aws_profile_name: "",
  access_key_id: "",
  secret_access_key: "",
};

function AccountForm({
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
        <CardTitle>{editing ? "Edit AWS account" : "Add AWS account"}</CardTitle>
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
          Close
        </Button>
      </CardHeader>
      <CardBody>
        <form onSubmit={submit} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label>AWS account ID (12 digits)</Label>
              <Input
                value={f.account_id}
                onChange={(e) => update("account_id", e.target.value)}
                pattern="\d{12}"
                placeholder="111111111111"
                required
                disabled={editing}
              />
            </div>
            <div>
              <Label>Display name</Label>
              <Input
                value={f.name}
                onChange={(e) => update("name", e.target.value)}
                placeholder="example-prod"
                required
              />
            </div>
            <div className="sm:col-span-2">
              <Label>Description</Label>
              <Input
                value={f.description}
                onChange={(e) => update("description", e.target.value)}
                placeholder="Production AWS account"
              />
            </div>
            <div>
              <Label>State S3 bucket</Label>
              <Input
                value={f.state_bucket}
                onChange={(e) => update("state_bucket", e.target.value)}
                placeholder="example-tfstate-prod"
                required
              />
            </div>
            <div>
              <Label>Bucket region</Label>
              <Input
                value={f.state_bucket_region}
                onChange={(e) => update("state_bucket_region", e.target.value)}
              />
            </div>
            <div>
              <Label>Default region (workspaces)</Label>
              <Input
                value={f.default_region}
                onChange={(e) => update("default_region", e.target.value)}
              />
            </div>
            <div className="sm:col-span-2">
              <Label>AWS profile name (optional)</Label>
              <Input
                value={f.aws_profile_name}
                onChange={(e) => update("aws_profile_name", e.target.value)}
                placeholder="e.g. devops — leave empty if your terraform doesn't reference a named profile"
              />
              <p className="mt-1 text-xs text-slate-500">
                When your terraform code declares <code className="font-mono">provider &quot;aws&quot; &#123; profile = &quot;...&quot; &#125;</code>,
                set the same profile name here. The executor will write the
                matching <code className="font-mono">~/.aws/credentials</code> section so the SDK
                doesn&apos;t error with <em>&quot;failed to get shared config profile&quot;</em>.
              </p>
            </div>
            <div className="sm:col-span-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-300">
              <strong>Credentials are encrypted at rest</strong> with the same Fernet/HKDF
              scheme used for config secrets. The plaintext values you enter here are sent
              over HTTPS, encrypted on the API server, and never returned in any subsequent
              API response. {editing && "Leave blank to keep the existing values."}
            </div>
            <div>
              <Label>Access key ID</Label>
              <Input
                type="password"
                value={f.access_key_id}
                onChange={(e) => update("access_key_id", e.target.value)}
                autoComplete="off"
                required={!editing}
                placeholder={editing ? "(unchanged)" : "AKIA…"}
              />
            </div>
            <div>
              <Label>Secret access key</Label>
              <Input
                type="password"
                value={f.secret_access_key}
                onChange={(e) => update("secret_access_key", e.target.value)}
                autoComplete="off"
                required={!editing}
                placeholder={editing ? "(unchanged)" : ""}
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
              {busy ? <><Spinner /> Saving…</> : editing ? "Save changes" : "Add account"}
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

export default function AwsAccounts() {
  const [accounts, setAccounts] = useState<AwsAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [showForm, setShowForm] = useState<null | { mode: "create" | "edit"; row?: AwsAccount }>(null);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, { ok: boolean; detail?: string; bucket_exists?: boolean; caller_arn?: string }>>({});
  const [bucketBusy, setBucketBusy] = useState<Record<string, boolean>>({});
  // Delete awaits in-app confirmation (no native popups).
  const [pendingDelete, setPendingDelete] = useState<AwsAccount | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const r = await api.get("/v1/aws-accounts");
      setAccounts(r.data);
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
      await api.post("/v1/aws-accounts", {
        account_id: f.account_id,
        name: f.name,
        description: f.description || undefined,
        state_bucket: f.state_bucket,
        state_bucket_region: f.state_bucket_region,
        default_region: f.default_region,
        aws_profile_name: f.aws_profile_name || null,
        access_key_id: f.access_key_id,
        secret_access_key: f.secret_access_key,
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
        state_bucket: f.state_bucket,
        state_bucket_region: f.state_bucket_region,
        default_region: f.default_region,
        aws_profile_name: f.aws_profile_name || null,
      };
      if (f.access_key_id) body.access_key_id = f.access_key_id;
      if (f.secret_access_key) body.secret_access_key = f.secret_access_key;
      await api.put(`/v1/aws-accounts/${showForm.row.id}`, body);
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
      await api.delete(`/v1/aws-accounts/${pendingDelete.id}`);
      setPendingDelete(null);
      await load();
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? "Delete failed");
    } finally {
      setDeleteBusy(false);
    }
  }

  async function onTest(row: AwsAccount) {
    setTestResult((p) => ({ ...p, [row.id]: { ok: false, detail: "Testing…" } }));
    try {
      const r = await api.post(`/v1/aws-accounts/${row.id}/test`);
      setTestResult((p) => ({ ...p, [row.id]: r.data }));
    } catch (e: any) {
      setTestResult((p) => ({
        ...p,
        [row.id]: { ok: false, detail: e?.response?.data?.detail ?? e?.message ?? "Test failed" },
      }));
    }
  }

  async function onCreateBucket(row: AwsAccount) {
    setBucketBusy((p) => ({ ...p, [row.id]: true }));
    try {
      const r = await api.post(`/v1/aws-accounts/${row.id}/bucket`);
      const data = r.data;
      setTestResult((p) => ({
        ...p,
        [row.id]: {
          ok: data.ok,
          detail: data.detail,
          bucket_exists: data.ok,
          caller_arn: testResult[row.id]?.caller_arn,
        },
      }));
    } catch (e: any) {
      setTestResult((p) => ({
        ...p,
        [row.id]: {
          ok: false,
          detail: e?.response?.data?.detail ?? e?.message ?? "Create bucket failed",
        },
      }));
    } finally {
      setBucketBusy((p) => ({ ...p, [row.id]: false }));
    }
  }

  return (
    <div>
      <SectionHeader
        title="AWS Accounts"
        subtitle="Each AWS account has its own dedicated S3 state bucket. Credentials are encrypted at rest."
        action={!showForm && <Button onClick={() => setShowForm({ mode: "create" })}>+ Add AWS account</Button>}
      />

      {showForm && (
        <AccountForm
          initial={
            showForm.row
              ? {
                  account_id: showForm.row.account_id,
                  name: showForm.row.name,
                  description: showForm.row.description ?? "",
                  state_bucket: showForm.row.state_bucket,
                  state_bucket_region: showForm.row.state_bucket_region,
                  default_region: showForm.row.default_region,
                  aws_profile_name: showForm.row.aws_profile_name ?? "",
                  access_key_id: "",
                  secret_access_key: "",
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
      ) : accounts.length === 0 ? (
        <EmptyState
          title="No AWS accounts configured yet"
          description="Add an account to onboard its state bucket and credentials. Workspaces under that account will then read/write state through it."
          action={<Button onClick={() => setShowForm({ mode: "create" })}>+ Add AWS account</Button>}
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {accounts.map((acc) => {
            const tr = testResult[acc.id];
            return (
              <Card key={acc.id}>
                <CardHeader>
                  <div className="flex min-w-0 items-center gap-3">
                    <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-orange-100 text-sm font-semibold text-orange-700 dark:bg-orange-900/40 dark:text-orange-300">
                      AWS
                    </span>
                    <div className="min-w-0">
                      <CardTitle className="truncate">{acc.name}</CardTitle>
                      <p className="font-mono text-xs text-slate-500">{acc.account_id}</p>
                    </div>
                  </div>
                  <Badge tone="info">{acc.default_region}</Badge>
                </CardHeader>
                <CardBody className="space-y-3">
                  {acc.description && <p className="text-sm text-slate-500 dark:text-slate-400">{acc.description}</p>}
                  <div className="grid grid-cols-2 gap-2 text-sm">
                    <div>
                      <p className="text-xs uppercase tracking-wider text-slate-500">State bucket</p>
                      <p className="truncate font-mono text-slate-700 dark:text-slate-300">{acc.state_bucket}</p>
                    </div>
                    <div>
                      <p className="text-xs uppercase tracking-wider text-slate-500">Bucket region</p>
                      <p className="truncate font-mono text-slate-700 dark:text-slate-300">{acc.state_bucket_region}</p>
                    </div>
                    <div className="col-span-2">
                      <p className="text-xs uppercase tracking-wider text-slate-500">Access key</p>
                      <p className="font-mono text-slate-700 dark:text-slate-300">{acc.access_key_id_masked}</p>
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
                          ✓ {tr.caller_arn ?? "Credentials valid"}
                          {tr.bucket_exists !== undefined && (
                            <span> · bucket {tr.bucket_exists ? "✓" : "missing"}</span>
                          )}
                          {tr.detail && <span> — {tr.detail}</span>}
                        </p>
                      ) : (
                        <p>✕ {tr.detail ?? "Credentials test failed"}</p>
                      )}
                    </div>
                  )}
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" variant="secondary" onClick={() => onTest(acc)}>
                      Test credentials
                    </Button>
                    {tr?.ok && tr.bucket_exists === false && (
                      <Button
                        size="sm"
                        variant="primary"
                        onClick={() => onCreateBucket(acc)}
                        disabled={bucketBusy[acc.id]}
                      >
                        {bucketBusy[acc.id] ? "Creating…" : "Create bucket"}
                      </Button>
                    )}
                    <Button size="sm" variant="ghost" onClick={() => setShowForm({ mode: "edit", row: acc })}>
                      Edit
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="ml-auto text-red-500 hover:text-red-400"
                      onClick={() => setPendingDelete(acc)}
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
        title="Delete AWS account"
        message={
          <>
            Delete AWS account{" "}
            <strong>
              {pendingDelete?.account_id} ({pendingDelete?.name})
            </strong>
            ? Workspaces tied to this account will be unable to read/write state until reconfigured.
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
