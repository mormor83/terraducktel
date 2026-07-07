/**
 * Settings → Policies. BU-scoped OPA/conftest rule management:
 *   - The gate config (mode enforce/warn/off, bundled + git sources) → /v1/integrations/opa
 *   - A list of DB-authored policies → /v1/policies (CRUD, admin)
 *   - An editor with three tabs: Rule (+ dry-run vs a plan), Unit tests, History (diff + restore)
 *
 * Mirrors the SecuritySection (Checkov) patterns: load on mount, optimistic
 * save, masked errors, ScopeBadge for the active BU.
 */
import { useEffect, useState } from "react";

import { api } from "../../api/client";
import { useBusinessUnits, useBusinessUnitSelection } from "../../hooks/useBusinessUnit";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  ConfirmDialog,
  Input,
  Label,
  Select,
  Spinner,
  cx,
} from "../ui";
import { RegoDiff, RegoEditor } from "../RegoEditor";

type Severity = "block" | "warn" | "info";
type Mode = "enforce" | "warn" | "off";

type Policy = {
  id: string;
  name: string;
  description: string | null;
  rego: string;
  tests_rego: string | null;
  severity: Severity;
  enabled: boolean;
  current_version: number;
  updated_at: string;
};

type PolicyVersion = {
  id: string;
  version: number;
  rego: string;
  tests_rego: string | null;
  severity: Severity;
  created_at: string;
  changed_by: string | null;
};

type Violation = {
  policy: string;
  severity: Severity;
  level: "deny" | "warn";
  msg: string;
  resource: string | null;
};

const SEVERITY_TONE: Record<Severity, "danger" | "warning" | "info"> = {
  block: "danger",
  warn: "warning",
  info: "info",
};

const STARTER_REGO = `package main

import future.keywords.if

deny[msg] if {
\tresource := input.resource_changes[_]
\t# ... your condition ...
\tmsg := sprintf("violation on %s", [resource.address])
}
`;

function Toggle({
  checked,
  onChange,
  label,
  busy = false,
}: {
  checked: boolean;
  onChange: () => void;
  label: string;
  busy?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      title={checked ? "Enabled — click to disable" : "Disabled — click to enable"}
      disabled={busy}
      onClick={onChange}
      className={cx(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:opacity-50",
        checked ? "bg-emerald-500" : "bg-slate-300 dark:bg-slate-600",
      )}
    >
      <span
        className={cx(
          "inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-4" : "translate-x-0.5",
        )}
      />
    </button>
  );
}

function ScopeBadge() {
  const [slug] = useBusinessUnitSelection();
  const { bus } = useBusinessUnits();
  const label = slug ? bus.find((b) => b.slug === slug)?.name ?? slug : "(no BU selected)";
  return (
    <div className="mb-3 inline-flex items-center gap-2 rounded-md border border-brand-border bg-brand-surface2 px-2.5 py-1 text-xs text-brand-textSoft dark:border-brand-700 dark:bg-brand-800/40 dark:text-brand-100/80">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
        <path d="M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V7M3 7l9-4 9 4M3 7l9 4 9-4" />
      </svg>
      Scoped to BU: <strong className="text-brand-text dark:text-brand-100">{label}</strong>
    </div>
  );
}

function errText(e: any): string {
  return e?.response?.data?.detail ?? e?.message ?? "Request failed";
}

// ─── gate config ─────────────────────────────────────────────────────────────

function GateConfig() {
  const [mode, setMode] = useState<Mode>("off");
  const [useBundled, setUseBundled] = useState(true);
  const [bundledSev, setBundledSev] = useState<Severity>("block");
  const [gitSev, setGitSev] = useState<Severity>("block");
  const [repoUrl, setRepoUrl] = useState("");
  const [repoRef, setRepoRef] = useState("main");
  const [repoDir, setRepoDir] = useState("");
  const [inherited, setInherited] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function load() {
    try {
      const r = await api.get("/v1/integrations/opa");
      setMode(r.data.mode);
      setUseBundled(r.data.use_bundled);
      setBundledSev(r.data.bundled_severity);
      setGitSev(r.data.git_severity);
      setRepoUrl(r.data.repo_url ?? "");
      setRepoRef(r.data.repo_ref ?? "main");
      setRepoDir(r.data.repo_dir ?? "");
      setInherited(!!r.data.inherited);
    } catch (e) {
      setError(errText(e));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { load(); }, []);

  async function save() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await api.put("/v1/integrations/opa", {
        mode,
        use_bundled: useBundled,
        bundled_severity: bundledSev,
        git_severity: gitSev,
        repo_url: repoUrl,
        repo_ref: repoRef,
        repo_dir: repoDir,
      });
      setInherited(false);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(errText(e));
    } finally {
      setSaving(false);
    }
  }

  const modeTone = mode === "enforce" ? "danger" : mode === "warn" ? "warning" : "neutral";

  return (
    <Card>
      <CardHeader>
        <CardTitle>OPA policy gate</CardTitle>
        <div className="flex items-center gap-2">
          {inherited && <Badge tone="warning">inherited</Badge>}
          <Badge tone={modeTone}>{mode}</Badge>
        </div>
      </CardHeader>
      <CardBody className="space-y-4">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          After <code className="font-mono text-xs">terraform plan</code>, the executor evaluates
          the plan with <a href="https://www.conftest.dev/" target="_blank" rel="noreferrer noopener" className="text-brand-600 underline-offset-2 hover:underline">conftest</a> against
          your policies. <strong>Enforce</strong> blocks the run before approval when a
          <Badge tone="danger">block</Badge>-severity rule fails; <strong>warn</strong> records
          findings without blocking.
        </p>

        {loading ? (
          <p className="text-sm italic text-slate-500">Loading…</p>
        ) : (
          <>
            <div className="grid grid-cols-3 gap-2">
              {(["enforce", "warn", "off"] as Mode[]).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  className={cx(
                    "rounded-lg border p-3 text-center text-sm font-medium transition-colors",
                    mode === m
                      ? "border-brand-500 bg-brand-50 dark:bg-brand-900/30 dark:border-brand-600"
                      : "border-slate-200 hover:bg-slate-50 dark:border-slate-700/70 dark:hover:bg-slate-800/40",
                  )}
                >
                  {m}
                </button>
              ))}
            </div>

            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={useBundled} onChange={(e) => setUseBundled(e.target.checked)} />
              Include bundled default policies
            </label>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label htmlFor="bundled-sev">Bundled severity</Label>
                <Select id="bundled-sev" value={bundledSev} onChange={(e) => setBundledSev(e.target.value as Severity)}>
                  <option value="block">block</option>
                  <option value="warn">warn</option>
                  <option value="info">info</option>
                </Select>
              </div>
              <div>
                <Label htmlFor="git-sev">Git repo severity</Label>
                <Select id="git-sev" value={gitSev} onChange={(e) => setGitSev(e.target.value as Severity)}>
                  <option value="block">block</option>
                  <option value="warn">warn</option>
                  <option value="info">info</option>
                </Select>
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="repo-url">Optional git policy-repo</Label>
              <Input id="repo-url" placeholder="https://github.com/org/policies.git" value={repoUrl} onChange={(e) => setRepoUrl(e.target.value)} />
              <div className="grid grid-cols-2 gap-3">
                <Input placeholder="ref (main)" value={repoRef} onChange={(e) => setRepoRef(e.target.value)} />
                <Input placeholder="subdir (optional)" value={repoDir} onChange={(e) => setRepoDir(e.target.value)} />
              </div>
            </div>

            <div className="flex items-center gap-3">
              <Button onClick={save} disabled={saving}>
                {saving ? <><Spinner /> Saving…</> : "Save gate config"}
              </Button>
              {saved && <span className="text-sm text-emerald-600">✓ Saved</span>}
            </div>
          </>
        )}
        {error && <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">{error}</div>}
      </CardBody>
    </Card>
  );
}

// ─── dry-run panel ───────────────────────────────────────────────────────────

function ViolationTable({ result }: { result: { ok: boolean; violations: Violation[]; warnings: Violation[]; engine_error?: string | null } }) {
  if (result.engine_error) {
    return <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">conftest error: {result.engine_error}</div>;
  }
  const rows = [...result.violations, ...result.warnings];
  if (rows.length === 0) {
    return <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-300">✓ No findings — the plan passes these policies.</div>;
  }
  return (
    <table className="w-full text-left text-sm">
      <thead className="text-xs uppercase text-slate-500">
        <tr><th className="py-1 pr-2">Policy</th><th className="pr-2">Severity</th><th className="pr-2">Resource</th><th>Message</th></tr>
      </thead>
      <tbody>
        {rows.map((v, i) => (
          <tr key={i} className="border-t border-slate-100 dark:border-slate-800">
            <td className="py-1 pr-2 font-mono text-xs">{v.policy}</td>
            <td className="pr-2"><Badge tone={SEVERITY_TONE[v.severity]}>{v.severity}</Badge></td>
            <td className="pr-2 font-mono text-xs">{v.resource ?? "—"}</td>
            <td>{v.msg}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function DryRunPanel({ rego, severity, name }: { rego: string; severity: Severity; name: string }) {
  const [runs, setRuns] = useState<{ id: string; workspace_id: string; command: string }[]>([]);
  const [runId, setRunId] = useState("");
  const [planJson, setPlanJson] = useState("");
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get("/v1/runs").then((r) => setRuns((r.data ?? []).slice(0, 30))).catch(() => {});
  }, []);

  async function run() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const body: any = { rego, rego_name: name || "candidate", rego_severity: severity };
      if (runId) body.run_id = runId;
      else if (planJson.trim()) body.plan_json = planJson;
      else { setError("Pick a run or paste plan JSON"); setBusy(false); return; }
      const r = await api.post("/v1/policies/test", body);
      setResult(r.data);
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 rounded-lg border border-slate-200 p-3 dark:border-slate-700/70">
      <div className="text-sm font-medium">Dry-run against a plan</div>
      <div>
        <Label htmlFor="dry-run">Recent run</Label>
        <Select id="dry-run" value={runId} onChange={(e) => setRunId(e.target.value)}>
          <option value="">— paste plan JSON below —</option>
          {runs.map((r) => (
            <option key={r.id} value={r.id}>{r.command} · {r.id.slice(0, 8)}</option>
          ))}
        </Select>
      </div>
      {!runId && (
        <textarea
          className="h-28 w-full rounded-md border border-slate-200 p-2 font-mono text-xs dark:border-slate-700 dark:bg-slate-900"
          placeholder='Paste `terraform show -json tfplan` output…'
          value={planJson}
          onChange={(e) => setPlanJson(e.target.value)}
        />
      )}
      <Button size="sm" onClick={run} disabled={busy}>{busy ? <><Spinner /> Evaluating…</> : "Evaluate"}</Button>
      {error && <div className="text-sm text-red-600">{error}</div>}
      {result && <ViolationTable result={result} />}
    </div>
  );
}

// ─── editor (Rule / Unit tests / History) ────────────────────────────────────

function PolicyEditor({ policy, onClose, onSaved }: { policy: Policy | null; onClose: () => void; onSaved: () => void }) {
  const isNew = policy === null;
  const [tab, setTab] = useState<"rule" | "tests" | "history">("rule");
  const [name, setName] = useState(policy?.name ?? "");
  const [description, setDescription] = useState(policy?.description ?? "");
  const [severity, setSeverity] = useState<Severity>(policy?.severity ?? "block");
  const [rego, setRego] = useState(policy?.rego ?? STARTER_REGO);
  const [testsRego, setTestsRego] = useState(policy?.tests_rego ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Unit-test results
  const [verifyResult, setVerifyResult] = useState<{ ok: boolean; passed: number; failures: string[]; engine_error?: string | null } | null>(null);
  const [verifying, setVerifying] = useState(false);

  // History
  const [versions, setVersions] = useState<PolicyVersion[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<PolicyVersion | null>(null);

  async function loadVersions() {
    if (!policy) return;
    try {
      const r = await api.get(`/v1/policies/${policy.id}/versions`);
      setVersions(r.data ?? []);
    } catch { /* noop */ }
  }
  useEffect(() => { if (tab === "history") loadVersions(); }, [tab]);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const body = { name, description, severity, rego, tests_rego: testsRego || null };
      if (isNew) await api.post("/v1/policies", body);
      else await api.put(`/v1/policies/${policy!.id}`, body);
      onSaved();
    } catch (e) {
      setError(errText(e));
    } finally {
      setSaving(false);
    }
  }

  async function verify() {
    setVerifying(true);
    setVerifyResult(null);
    try {
      const r = await api.post("/v1/policies/verify", { rego, tests_rego: testsRego });
      setVerifyResult(r.data);
    } catch (e) {
      setVerifyResult({ ok: false, passed: 0, failures: [errText(e)] });
    } finally {
      setVerifying(false);
    }
  }

  async function restore(version: number) {
    if (!policy) return;
    try {
      await api.post(`/v1/policies/${policy.id}/versions/${version}/restore`);
      onSaved();
    } catch (e) {
      setError(errText(e));
    }
  }

  const tabs: { id: typeof tab; label: string; show: boolean }[] = [
    { id: "rule", label: "Rule", show: true },
    { id: "tests", label: "Unit tests", show: true },
    { id: "history", label: "History", show: !isNew },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle>{isNew ? "New policy" : `Edit · ${policy!.name}`}</CardTitle>
        <Button variant="ghost" size="sm" onClick={onClose}>Back to list</Button>
      </CardHeader>
      <CardBody className="space-y-4">
        <div className="flex gap-1 border-b border-slate-200 dark:border-slate-700">
          {tabs.filter((t) => t.show).map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              className={cx(
                "px-3 py-1.5 text-sm font-medium",
                tab === t.id ? "border-b-2 border-brand-500 text-brand-700 dark:text-brand-200" : "text-slate-500",
              )}
            >
              {t.label}
            </button>
          ))}
        </div>

        {tab === "rule" && (
          <>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label htmlFor="p-name">Name</Label>
                <Input id="p-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="no-public-buckets" />
              </div>
              <div>
                <Label htmlFor="p-sev">Severity</Label>
                <Select id="p-sev" value={severity} onChange={(e) => setSeverity(e.target.value as Severity)}>
                  <option value="block">block — fails the run under enforce</option>
                  <option value="warn">warn — advisory</option>
                  <option value="info">info — informational</option>
                </Select>
              </div>
            </div>
            <div>
              <Label htmlFor="p-desc">Description</Label>
              <Input id="p-desc" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What this rule enforces" />
            </div>
            <div>
              <Label htmlFor="p-rego">Rego</Label>
              <RegoEditor value={rego} onChange={setRego} />
            </div>
            <DryRunPanel rego={rego} severity={severity} name={name} />
          </>
        )}

        {tab === "tests" && (
          <>
            <p className="text-sm text-slate-500">
              Author <code className="font-mono text-xs">test_*</code> rules; <strong>Run tests</strong> executes
              {" "}<code className="font-mono text-xs">conftest verify</code> against the rule above.
            </p>
            <RegoEditor value={testsRego} onChange={setTestsRego} />
            <Button size="sm" onClick={verify} disabled={verifying}>{verifying ? <><Spinner /> Running…</> : "Run tests"}</Button>
            {verifyResult && (
              verifyResult.engine_error
                ? <div className="text-sm text-red-600">conftest error: {verifyResult.engine_error}</div>
                : verifyResult.ok
                  ? <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-300">✓ {verifyResult.passed} test(s) passed</div>
                  : <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
                      {verifyResult.failures.length} failing:<ul className="ml-4 list-disc">{verifyResult.failures.map((f, i) => <li key={i}>{f}</li>)}</ul>
                    </div>
            )}
          </>
        )}

        {tab === "history" && (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2">
              {versions.map((v) => (
                <button
                  key={v.id}
                  type="button"
                  onClick={() => setSelectedVersion(v)}
                  className={cx(
                    "rounded-md border px-2 py-1 text-xs",
                    selectedVersion?.id === v.id ? "border-brand-500 bg-brand-50 dark:bg-brand-900/30" : "border-slate-200 dark:border-slate-700",
                    v.version === policy?.current_version && "font-semibold",
                  )}
                >
                  v{v.version}{v.version === policy?.current_version ? " (current)" : ""}
                </button>
              ))}
            </div>
            {selectedVersion && selectedVersion.version !== policy?.current_version && (
              <>
                <RegoDiff original={selectedVersion.rego} modified={rego} height={260} />
                <Button size="sm" variant="secondary" onClick={() => restore(selectedVersion.version)}>
                  Restore v{selectedVersion.version}
                </Button>
              </>
            )}
          </div>
        )}

        {error && <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">{error}</div>}

        {tab !== "history" && (
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose}>Cancel</Button>
            <Button onClick={save} disabled={saving || !name || !rego}>{saving ? <><Spinner /> Saving…</> : isNew ? "Create policy" : "Save changes"}</Button>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

// ─── list + section root ─────────────────────────────────────────────────────

function PolicyList({ onEdit, onNew }: { onEdit: (p: Policy) => void; onNew: () => void }) {
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<Policy | null>(null);
  const [togglingId, setTogglingId] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    try {
      const r = await api.get("/v1/policies");
      setPolicies(r.data ?? []);
      setError(null);
    } catch (e) {
      setError(errText(e));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { load(); }, []);

  async function toggle(p: Policy) {
    setTogglingId(p.id);
    // Optimistic flip so the switch responds instantly.
    setPolicies((prev) => prev.map((x) => (x.id === p.id ? { ...x, enabled: !x.enabled } : x)));
    try {
      await api.put(`/v1/policies/${p.id}`, { enabled: !p.enabled });
    } catch (e) {
      setError(errText(e));
      load(); // revert on failure
    } finally {
      setTogglingId(null);
    }
  }

  async function doDelete() {
    if (!confirmDelete) return;
    try {
      await api.delete(`/v1/policies/${confirmDelete.id}`);
      setConfirmDelete(null);
      load();
    } catch (e) {
      setError(errText(e));
      setConfirmDelete(null);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Custom policies</CardTitle>
        <Button size="sm" onClick={onNew}>New policy</Button>
      </CardHeader>
      <CardBody className="space-y-3">
        {loading ? (
          <p className="text-sm italic text-slate-500">Loading…</p>
        ) : policies.length === 0 ? (
          <p className="text-sm text-slate-500">No custom policies yet. Bundled defaults still apply when the gate is on.</p>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {policies.map((p) => (
              <li key={p.id} className="flex items-center justify-between gap-3 py-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className={cx("font-medium", !p.enabled && "text-slate-400")}>{p.name}</span>
                    <Badge tone={SEVERITY_TONE[p.severity]}>{p.severity}</Badge>
                    <span className="text-xs text-slate-400">v{p.current_version}</span>
                  </div>
                  {p.description && <p className="truncate text-xs text-slate-500">{p.description}</p>}
                </div>
                <div className="flex shrink-0 items-center gap-3">
                  <div className="flex items-center gap-1.5">
                    <Toggle
                      checked={p.enabled}
                      busy={togglingId === p.id}
                      onChange={() => toggle(p)}
                      label={`${p.enabled ? "Disable" : "Enable"} ${p.name}`}
                    />
                    <span className="w-12 text-xs text-slate-500">{p.enabled ? "enabled" : "disabled"}</span>
                  </div>
                  <Button size="sm" variant="secondary" onClick={() => onEdit(p)}>Edit</Button>
                  <Button size="sm" variant="danger" onClick={() => setConfirmDelete(p)}>Delete</Button>
                </div>
              </li>
            ))}
          </ul>
        )}
        {error && <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">{error}</div>}
      </CardBody>
      <ConfirmDialog
        open={confirmDelete !== null}
        title="Delete policy?"
        message={`"${confirmDelete?.name}" will be removed. This cannot be undone.`}
        tone="danger"
        onConfirm={doDelete}
        onCancel={() => setConfirmDelete(null)}
      />
    </Card>
  );
}

export default function PoliciesSection() {
  // `undefined` = list view; `null` = new; Policy = editing.
  const [editing, setEditing] = useState<Policy | null | undefined>(undefined);
  const [listKey, setListKey] = useState(0);

  if (editing !== undefined) {
    return (
      <div>
        <ScopeBadge />
        <PolicyEditor
          policy={editing}
          onClose={() => setEditing(undefined)}
          onSaved={() => { setEditing(undefined); setListKey((k) => k + 1); }}
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <ScopeBadge />
      <GateConfig />
      <PolicyList key={listKey} onEdit={(p) => setEditing(p)} onNew={() => setEditing(null)} />
    </div>
  );
}
