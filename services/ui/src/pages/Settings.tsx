import { FormEvent, ReactNode, Suspense, lazy, useEffect, useMemo, useState } from "react";
import CloudProviders from "./CloudProviders";
import ApiKeysSection from "../components/settings/ApiKeysSection";
// Lazy — pulls in the Monaco rego editor; keep it out of the main bundle so it
// only loads when an admin opens the Policies tab.
const PoliciesSection = lazy(() => import("../components/settings/PoliciesSection"));
import { VariablesPanel } from "../components/VariablesPanel";
import { workspacePathSegments } from "../components/WorkspaceTree";
import {
  api,
  getSavedCredentials,
  setSavedCredentials,
  setToken,
} from "../api/client";
import { useCurrentUser, hasMinRole, type UserRole } from "../hooks/useAuth";
import { useBusinessUnits, useBusinessUnitSelection } from "../hooks/useBusinessUnit";
import { useTheme, type Theme } from "../hooks/useTheme";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  ConfirmDialog,
  cx,
  Input,
  Label,
  SectionHeader,
  Spinner,
} from "../components/ui";

/**
 * Small badge that names the Business Unit a section's data is scoped to.
 * Used at the top of per-BU Settings tabs (GitHub, Modules, Cloud Providers).
 */
function ScopeBadge() {
  const [slug] = useBusinessUnitSelection();
  const { bus } = useBusinessUnits();
  const label = (() => {
    if (slug === null || slug === "") return "(no BU selected)";
    return bus.find((b) => b.slug === slug)?.name ?? slug;
  })();
  return (
    <div className="mb-3 inline-flex items-center gap-2 rounded-md border border-brand-border bg-brand-surface2 px-2.5 py-1 text-xs text-brand-textSoft dark:border-brand-700 dark:bg-brand-800/40 dark:text-brand-100/80">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
        <path d="M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V7M3 7l9-4 9 4M3 7l9 4 9-4" />
      </svg>
      Scoped to BU: <strong className="text-brand-text dark:text-brand-100">{label}</strong>
    </div>
  );
}

// ─── theme picker ──────────────────────────────────────────────────────────

function ThemePicker() {
  const { theme, setTheme } = useTheme();
  const options: { id: Theme; label: string; icon: JSX.Element }[] = [
    {
      id: "light",
      label: "Light",
      icon: (
        <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden>
          <path d="M12 18a6 6 0 1 0 0-12 6 6 0 0 0 0 12Zm0 4a1 1 0 0 1-1-1v-1a1 1 0 1 1 2 0v1a1 1 0 0 1-1 1Zm0-19a1 1 0 0 1 1 1v1a1 1 0 1 1-2 0V4a1 1 0 0 1 1-1Zm9 9a1 1 0 0 1-1 1h-1a1 1 0 1 1 0-2h1a1 1 0 0 1 1 1ZM4 12a1 1 0 0 1-1 1H2a1 1 0 1 1 0-2h1a1 1 0 0 1 1 1Zm14.485 6.485a1 1 0 0 1-1.414 0l-.707-.707a1 1 0 1 1 1.414-1.414l.707.707a1 1 0 0 1 0 1.414Zm-12.728-12.728a1 1 0 0 1-1.414 0L3.636 5.05a1 1 0 0 1 1.414-1.414l.707.707a1 1 0 0 1 0 1.414Zm12.728-1.414a1 1 0 0 1 0 1.414l-.707.707a1 1 0 1 1-1.414-1.414l.707-.707a1 1 0 0 1 1.414 0ZM5.05 18.95a1 1 0 0 1 0-1.414l.707-.707a1 1 0 1 1 1.414 1.414l-.707.707a1 1 0 0 1-1.414 0Z" />
        </svg>
      ),
    },
    {
      id: "dark",
      label: "Dark",
      icon: (
        <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden>
          <path d="M21.752 15.002A9 9 0 1 1 12 3a7 7 0 0 0 9.752 12.002Z" />
        </svg>
      ),
    },
    {
      id: "system",
      label: "System",
      icon: (
        <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden>
          <path d="M4 5h16a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1h-7v2h3a1 1 0 1 1 0 2H8a1 1 0 1 1 0-2h3v-2H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Z" />
        </svg>
      ),
    },
  ];
  return (
    <div className="grid grid-cols-3 gap-2">
      {options.map((o) => {
        const active = theme === o.id;
        return (
          <button
            key={o.id}
            type="button"
            onClick={() => setTheme(o.id)}
            className={cx(
              "flex flex-col items-center gap-1 rounded-lg border px-3 py-3 text-sm font-medium transition-colors",
              active
                ? "border-sky-500 bg-sky-50 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300 dark:border-sky-700"
                : "border-slate-200 text-slate-600 hover:bg-slate-50 dark:border-slate-700/70 dark:text-slate-400 dark:hover:bg-slate-800/60",
            )}
          >
            {o.icon}
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

// ─── account section ───────────────────────────────────────────────────────

function AccountSection() {
  const user = useCurrentUser();
  function logout() {
    setToken(null);
    window.location.href = "/settings";
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Account</CardTitle>
      </CardHeader>
      <CardBody className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Email" value={user?.email ?? "—"} />
          <Field label="Role" value={user?.role ? <Badge tone="info">{user.role}</Badge> : "—"} />
          <Field label="User ID" value={<span className="font-mono text-xs">{user?.id ?? "—"}</span>} />
        </div>
        <div className="pt-2">
          <Button type="button" variant="danger" onClick={logout}>
            Sign out
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}

function Field({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center justify-between rounded-md border border-slate-200 bg-slate-50 px-3 py-2 dark:border-slate-800 dark:bg-slate-900/50">
      <span className="text-xs uppercase tracking-wider text-slate-500">{label}</span>
      <span className="ml-3 truncate text-sm">{value}</span>
    </div>
  );
}

function AppearanceSection() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Appearance</CardTitle>
      </CardHeader>
      <CardBody className="space-y-3">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Choose between light, dark, or follow your operating system.
        </p>
        <ThemePicker />
      </CardBody>
    </Card>
  );
}

function PersistentLoginSection() {
  const [hasSaved, setHasSaved] = useState(() => !!getSavedCredentials());
  const [cleared, setCleared] = useState(false);
  function clearSavedCreds() {
    setSavedCredentials(null);
    setHasSaved(false);
    setCleared(true);
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Persistent login</CardTitle>
      </CardHeader>
      <CardBody className="space-y-3">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          When checked on the login screen, your email and password are stored
          in the browser so you don&apos;t have to retype them.{" "}
          <strong>Testing-only</strong> — SSO via OIDC will replace this.
        </p>
        <div className="flex items-center justify-between">
          <span className="text-sm">Status</span>
          {hasSaved ? <Badge tone="success">enabled</Badge> : <Badge tone="neutral">not saved</Badge>}
        </div>
        {cleared && (
          <p className="text-sm text-emerald-600 dark:text-emerald-400">Saved credentials cleared.</p>
        )}
        <Button type="button" variant="secondary" onClick={clearSavedCreds} disabled={!hasSaved}>
          Clear saved credentials
        </Button>
      </CardBody>
    </Card>
  );
}

// ─── github integration ────────────────────────────────────────────────────

type GitHubStatus = {
  configured: boolean;
  token_tail?: string | null;
  overridden_by_env?: boolean;
  inherited?: boolean;
};

type GitHubTestResult = {
  ok: boolean;
  detail?: string | null;
  login?: string | null;
  scopes?: string[] | null;
};

function GitHubSection() {
  const [status, setStatus] = useState<GitHubStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [token, setTokenInput] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<GitHubTestResult | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);

  async function load() {
    try {
      const r = await api.get("/v1/integrations/github");
      setStatus(r.data);
      setError(null);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Failed to load");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!token) return;
    setSubmitting(true);
    setError(null);
    setTest(null);
    try {
      await api.put("/v1/integrations/github", { token });
      setTokenInput("");
      setEditing(false);
      await load();
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Save failed");
    } finally {
      setSubmitting(false);
    }
  }

  async function remove() {
    setSubmitting(true);
    setError(null);
    try {
      await api.delete("/v1/integrations/github");
      setConfirmRemove(false);
      await load();
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Remove failed");
    } finally {
      setSubmitting(false);
    }
  }

  async function runTest() {
    setTesting(true);
    setTest(null);
    try {
      const r = await api.post("/v1/integrations/github/test");
      setTest(r.data);
    } catch (e: any) {
      setTest({ ok: false, detail: e?.response?.data?.detail ?? e?.message ?? "Test failed" });
    } finally {
      setTesting(false);
    }
  }

  return (
    <div>
      <ScopeBadge />
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden className="text-slate-700 dark:text-slate-300">
            <path d="M12 .3a12 12 0 0 0-3.79 23.4c.6.11.83-.26.83-.58v-2c-3.34.73-4.04-1.61-4.04-1.61-.55-1.39-1.34-1.76-1.34-1.76-1.09-.74.08-.73.08-.73 1.21.08 1.84 1.24 1.84 1.24 1.07 1.84 2.81 1.31 3.5 1 .11-.78.42-1.31.76-1.61-2.66-.3-5.46-1.33-5.46-5.93 0-1.31.47-2.38 1.24-3.22-.13-.3-.54-1.52.12-3.18 0 0 1.01-.32 3.3 1.23a11.5 11.5 0 0 1 6 0c2.29-1.55 3.3-1.23 3.3-1.23.66 1.66.25 2.88.12 3.18.77.84 1.24 1.91 1.24 3.22 0 4.61-2.81 5.62-5.48 5.92.43.37.81 1.1.81 2.22v3.29c0 .32.22.7.83.58A12 12 0 0 0 12 .3Z" />
          </svg>
          <CardTitle>GitHub credentials</CardTitle>
        </div>
        {status?.configured && !status.overridden_by_env && !status.inherited && <Badge tone="success">configured</Badge>}
        {status?.configured && status.inherited && <Badge tone="warning">inherited</Badge>}
        {status?.overridden_by_env && <Badge tone="warning">env override</Badge>}
        {!status?.configured && !status?.overridden_by_env && <Badge tone="neutral">not set</Badge>}
      </CardHeader>
      <CardBody className="space-y-4">
        {status?.inherited && (
          <div className="rounded-md border border-amber-300/50 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200">
            Inherited from the legacy global config. This BU hasn't saved its
            own token yet — replacing it here will write a BU-scoped value and
            stop the inheritance.
          </div>
        )}
        <p className="text-sm text-slate-500 dark:text-slate-400">
          A personal access token used by the executor to clone <em>private</em>{" "}
          terraform modules during a run. Encrypted at rest.{" "}
          <a href="https://github.com/settings/tokens" target="_blank" rel="noreferrer noopener" className="text-sky-600 underline-offset-2 hover:underline dark:text-sky-400">
            Generate a token →
          </a>{" "}
          (needs <code className="font-mono text-xs">repo</code> read scope).
        </p>

        {status?.overridden_by_env && (
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-300">
            <code className="font-mono">GITHUB_TOKEN</code> is set in the API container's environment, which overrides whatever's saved here. Unset it (or leave it blank in <code>.env</code>) to use the value saved on this page instead.
          </div>
        )}

        {loading ? (
          <p className="text-sm italic text-slate-500">Loading…</p>
        ) : status?.configured ? (
          <Field label="Token" value={<span className="font-mono">{status.token_tail ?? "configured"}</span>} />
        ) : null}

        {test && (
          <div className={"rounded-md border px-3 py-2 text-xs " + (test.ok ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-300" : "border-red-200 bg-red-50 text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300")}>
            {test.ok ? (
              <p>
                ✓ Authenticated as <strong>{test.login}</strong>
                {test.scopes && test.scopes.length > 0 && (<span> · scopes: <code className="font-mono">{test.scopes.join(", ")}</code></span>)}
              </p>
            ) : (
              <p>✕ {test.detail ?? "Test failed"}</p>
            )}
          </div>
        )}

        {error && (
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">{error}</div>
        )}

        {editing ? (
          <form onSubmit={save} className="space-y-3">
            <div>
              <Label htmlFor="github-token">Personal access token</Label>
              <Input id="github-token" type="password" value={token} onChange={(e) => setTokenInput(e.target.value)} placeholder="ghp_… or github_pat_…" autoComplete="off" required />
            </div>
            <div className="flex gap-2">
              <Button type="submit" disabled={submitting || !token}>{submitting ? <><Spinner /> Saving…</> : "Save token"}</Button>
              <Button type="button" variant="ghost" onClick={() => { setEditing(false); setTokenInput(""); }}>Cancel</Button>
            </div>
          </form>
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button type="button" onClick={() => setEditing(true)}>{status?.configured ? "Replace token" : "Add token"}</Button>
            {status?.configured && (<Button type="button" variant="secondary" onClick={runTest} disabled={testing}>{testing ? <><Spinner /> Testing…</> : "Test connection"}</Button>)}
            {status?.configured && (<Button type="button" variant="ghost" className="ml-auto text-red-500 hover:text-red-400" onClick={() => setConfirmRemove(true)} disabled={submitting}>Remove</Button>)}
          </div>
        )}
      </CardBody>
    </Card>
    <ConfirmDialog
      open={confirmRemove}
      tone="danger"
      title="Remove GitHub token"
      message={
        <>
          Remove the saved GitHub token? Future runs will fail to clone private terraform modules
          until a new token is set.
        </>
      }
      confirmLabel="Remove"
      busy={submitting}
      onConfirm={remove}
      onCancel={() => setConfirmRemove(false)}
    />
    </div>
  );
}

// ─── default base-infra repo (Git import prefill) ──────────────────────────

function InfraRepoSection() {
  const [repoUrl, setRepoUrl] = useState("");
  const [inherited, setInherited] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  async function load() {
    try {
      const r = await api.get("/v1/integrations/infra-repo");
      setRepoUrl(r.data?.repo_url ?? "");
      setInherited(!!r.data?.inherited);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Failed to load");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function save(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      await api.put("/v1/integrations/infra-repo", { repo_url: repoUrl.trim() });
      setInherited(false);
      setSaved("Saved");
      setTimeout(() => setSaved(null), 2000);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <ScopeBadge />
      <Card>
        <CardHeader>
          <CardTitle>Default infra repository</CardTitle>
          {inherited && <Badge tone="warning">inherited</Badge>}
        </CardHeader>
        <CardBody>
          {inherited && (
            <div className="mb-4 rounded-md border border-amber-300/50 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200">
              Inherited from the legacy global config. Saving here writes a
              BU-scoped value.
            </div>
          )}
          <p className="mb-4 text-sm text-slate-500 dark:text-slate-400">
            Prefills the repository URL on the{" "}
            <strong>Import workspaces from Git</strong> form so operators don&apos;t
            retype it each time. Leave blank to show a placeholder instead.
          </p>
          {loading ? (
            <p className="text-sm italic text-slate-500">Loading…</p>
          ) : (
            <form onSubmit={save} className="space-y-4">
              <div>
                <Label>Repository URL</Label>
                <Input
                  value={repoUrl}
                  onChange={(e) => setRepoUrl(e.target.value)}
                  placeholder="https://github.com/your-org/terraform-infra.git"
                />
              </div>
              {error && (
                <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">{error}</div>
              )}
              {saved && (
                <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-300">✓ {saved}</div>
              )}
              <div className="flex gap-2">
                <Button type="submit" disabled={saving}>
                  {saving ? <><Spinner /> Saving…</> : "Save"}
                </Button>
              </div>
            </form>
          )}
        </CardBody>
      </Card>
    </div>
  );
}

// ─── modules registry ──────────────────────────────────────────────────────

type ModulesConfig = {
  mode: "github" | "local";
  upstream_url: string;
  local_host_dir: string;
  inherited?: boolean;
};

// ─── Variables (global + per-workspace) ────────────────────────────────────

type _WsLite = {
  id: string;
  name: string;
  environment?: string;
  region?: string;
  aws_account_id?: string;
  tf_working_dir?: string;
};

type _AccountLite = { account_id: string; name: string };

function VariablesSection({ canWrite }: { canWrite: boolean }) {
  // Two panels stacked. Global lives at /v1/variables; workspace at
  // /v1/workspaces/{id}/variables. The `VariablesPanel` component already
  // implements both modes — we just pick the workspace via a dropdown and
  // pass `workspaceId` through.
  const [workspaces, setWorkspaces] = useState<_WsLite[] | null>(null);
  const [accounts, setAccounts] = useState<_AccountLite[]>([]);
  const [selectedWs, setSelectedWs] = useState<string>("");
  const [filter, setFilter] = useState<string>("");
  const [wsErr, setWsErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Load workspaces + AWS accounts in parallel; account names are
        // used to label the `<optgroup>` entries in the picker.
        const [w, a] = await Promise.all([
          api.get<_WsLite[]>("/v1/workspaces"),
          api.get<_AccountLite[]>("/v1/aws-accounts").catch(() => ({ data: [] as _AccountLite[] })),
        ]);
        if (cancelled) return;
        setWorkspaces(w.data);
        setAccounts(a.data);
        // Stash + restore last selection so a refresh doesn't bounce the
        // operator back to the top of the list.
        const stashed = localStorage.getItem("td_settings_vars_ws");
        if (stashed && w.data.some((row) => row.id === stashed)) {
          setSelectedWs(stashed);
        }
      } catch (e: any) {
        if (!cancelled) setWsErr(e?.response?.data?.detail ?? e?.message ?? "Failed to load workspaces");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  function pickWs(id: string) {
    setSelectedWs(id);
    if (id) localStorage.setItem("td_settings_vars_ws", id);
    else localStorage.removeItem("td_settings_vars_ws");
  }

  // Group filtered workspaces by aws_account_id so the picker is navigable
  // for orgs with 20+ stacks. Within a group we sort by name, then label
  // each entry with env + region for disambiguation when names repeat
  // across regions (e.g. `monitoring · us-east-1` vs `monitoring · eu-west-1`).
  const accountNameById = useMemo(() => {
    const m = new Map<string, string>();
    for (const acc of accounts) m.set(acc.account_id, acc.name);
    return m;
  }, [accounts]);

  const groupedFiltered = useMemo(() => {
    if (!workspaces) return [];
    const q = filter.trim().toLowerCase();
    const matches = workspaces.filter((w) => {
      if (!q) return true;
      const hay = [
        w.name,
        w.environment ?? "",
        w.region ?? "",
        w.aws_account_id ?? "",
        accountNameById.get(w.aws_account_id ?? "") ?? "",
        w.tf_working_dir ?? "",
      ]
        .join(" ")
        .toLowerCase();
      return hay.includes(q);
    });
    const byAccount = new Map<string, _WsLite[]>();
    for (const w of matches) {
      const key = w.aws_account_id ?? "(no account)";
      const arr = byAccount.get(key) ?? [];
      arr.push(w);
      byAccount.set(key, arr);
    }
    // Sort accounts: "global" sentinel last (non-AWS bucket), the rest by
    // friendly name (when available) then by account id.
    const keys = [...byAccount.keys()].sort((a, b) => {
      if (a === "global" && b !== "global") return 1;
      if (b === "global" && a !== "global") return -1;
      const an = accountNameById.get(a) ?? a;
      const bn = accountNameById.get(b) ?? b;
      return an.localeCompare(bn);
    });
    return keys.map((acc) => ({
      accountId: acc,
      accountLabel:
        acc === "global"
          ? "Non-AWS (global)"
          : accountNameById.get(acc)
            ? `${accountNameById.get(acc)} (${acc})`
            : acc,
      rows: byAccount.get(acc)!.sort((a, b) => a.name.localeCompare(b.name)),
    }));
  }, [workspaces, filter, accountNameById]);

  const matchCount = groupedFiltered.reduce((s, g) => s + g.rows.length, 0);

  return (
    <div className="space-y-6">
      {/* Global */}
      <section>
        <div className="mb-2">
          <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Global variables</h3>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Apply to every run in every workspace. Lowest precedence — overridden by
            workspace-scoped and run-scoped values with the same key.
          </p>
        </div>
        <VariablesPanel mode="global" canWrite={canWrite} />
      </section>

      {/* Workspace-scoped */}
      <section>
        <div className="mb-2 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Workspace variables</h3>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Apply to every run in the selected workspace. Override global values
              with the same key; still overridden by run-scoped values.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="search"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter by name, account, region…"
              className="w-56 rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-950"
            />
            <label htmlFor="settings-vars-ws" className="text-xs text-slate-600 dark:text-slate-400">
              Workspace
            </label>
            <select
              id="settings-vars-ws"
              value={selectedWs}
              onChange={(e) => pickWs(e.target.value)}
              className="min-w-[22rem] rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-950"
            >
              <option value="">
                — select a workspace ({matchCount}
                {workspaces && filter.trim() ? ` of ${workspaces.length}` : ""}) —
              </option>
              {groupedFiltered.map((g) => (
                <optgroup key={g.accountId} label={g.accountLabel}>
                  {g.rows.map((w) => {
                    // Show the folder prefix so two workspaces with the same
                    // leaf name in different parents (`home-aws` under
                    // `cloudflare-tunnel/` vs a top-level `home-aws`) are
                    // visually distinct in the picker. `workspacePathSegments`
                    // strips the leading `account-…/region/…` so we don't
                    // repeat the optgroup header info on every row.
                    const { folders, leaf } = workspacePathSegments({
                      name: w.name,
                      region: w.region ?? "",
                      tf_working_dir: w.tf_working_dir,
                    } as any);
                    const prefix = folders.length ? folders.join("/") + "/" : "";
                    return (
                      <option key={w.id} value={w.id}>
                        {prefix}
                        {leaf}
                        {w.environment ? ` · ${w.environment}` : ""}
                        {w.region ? ` · ${w.region}` : ""}
                      </option>
                    );
                  })}
                </optgroup>
              ))}
            </select>
          </div>
        </div>
        {wsErr && (
          <div className="mb-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
            {wsErr}
          </div>
        )}
        {selectedWs ? (
          <VariablesPanel
            // Force a remount when the operator switches workspaces so the
            // panel's internal "editing"/"new" state doesn't carry across.
            key={selectedWs}
            mode="workspace"
            workspaceId={selectedWs}
            canWrite={canWrite}
          />
        ) : (
          <p className="text-xs italic text-slate-500">
            Pick a workspace above to view and edit its variables.
          </p>
        )}
      </section>
    </div>
  );
}

// ─── GitHub webhook (push → auto-trigger) ──────────────────────────────────

type WebhookConfig = {
  bu_slug: string;
  configured: boolean;
  secret_tail?: string | null;
  github_org?: string | null;
  webhook_path: string;
};

function WebhookSection() {
  const [status, setStatus] = useState<WebhookConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [secret, setSecret] = useState("");
  const [orgInput, setOrgInput] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function load() {
    try {
      const r = await api.get<WebhookConfig>("/v1/integrations/webhook");
      setStatus(r.data);
      setOrgInput(r.data.github_org ?? "");
      setError(null);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Failed to load");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function save(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      // Only send fields the operator actually changed; PATCH-y semantics.
      const body: Record<string, string> = {};
      if (secret) body.secret = secret;
      if (orgInput !== (status?.github_org ?? "")) body.github_org = orgInput;
      await api.put("/v1/integrations/webhook", body);
      setSecret("");
      setEditing(false);
      await load();
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Save failed");
    } finally {
      setSubmitting(false);
    }
  }

  // The API base for the deployed env. Production lives at PUBLIC_UI_URL
  // / PUBLIC_API_URL on the same host; in dev the API hostname is
  // localhost:8001. We compose from window.location so this UI works in
  // both without an extra env var read from the bundle.
  const fullWebhookUrl =
    status?.webhook_path
      ? `${window.location.origin}${status.webhook_path}`
      : "";

  return (
    <div>
      <ScopeBadge />
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden className="text-amber-500">
              {ICON.webhook}
            </svg>
            <CardTitle>GitHub push webhook</CardTitle>
          </div>
          {status?.configured ? (
            <Badge tone="success">configured</Badge>
          ) : (
            <Badge tone="neutral">not set</Badge>
          )}
        </CardHeader>
        <CardBody className="space-y-4">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            When a push lands on a branch tracked by a workspace with{" "}
            <strong>auto-trigger</strong> enabled (workspace expand row → checkbox), TDT
            launches a plan run automatically. Files changed in the push must touch the
            workspace's <code className="font-mono text-xs">tf_working_dir</code>.
            Per-BU HMAC secret protects the endpoint — set it here, then register the URL
            in your GitHub repo's <em>Settings → Webhooks → Add webhook</em>.
          </p>

          {loading ? (
            <p className="text-sm italic text-slate-500">Loading…</p>
          ) : status ? (
            <div className="grid gap-2 sm:grid-cols-2">
              <Field
                label="Webhook URL (paste into GitHub)"
                value={
                  fullWebhookUrl ? (
                    <span className="inline-flex items-center gap-2">
                      <code className="break-all font-mono text-[11px]">{fullWebhookUrl}</code>
                      <button
                        type="button"
                        onClick={() => navigator.clipboard.writeText(fullWebhookUrl)}
                        className="text-[11px] text-brand-500 hover:underline"
                        title="Copy to clipboard"
                      >
                        copy
                      </button>
                    </span>
                  ) : (
                    "—"
                  )
                }
              />
              <Field
                label="Secret"
                value={
                  status.configured ? (
                    <span className="font-mono">{status.secret_tail ?? "configured"}</span>
                  ) : (
                    <span className="text-amber-600">not set — webhook calls will 403</span>
                  )
                }
              />
              <Field
                label="GitHub org allowlist (optional)"
                value={
                  status.github_org ? (
                    <span className="font-mono">{status.github_org}</span>
                  ) : (
                    <span className="text-slate-500">any (no filter)</span>
                  )
                }
              />
              <Field
                label="GitHub content type"
                value={<span className="font-mono">application/json</span>}
              />
            </div>
          ) : null}

          {error && (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
              {error}
            </div>
          )}

          {editing ? (
            <form onSubmit={save} className="space-y-3">
              <div>
                <Label htmlFor="webhook-secret">Webhook secret</Label>
                <Input
                  id="webhook-secret"
                  type="password"
                  value={secret}
                  onChange={(e) => setSecret(e.target.value)}
                  placeholder={status?.configured ? "Leave blank to keep existing" : "min 8 chars"}
                  autoComplete="off"
                />
                <p className="mt-1 text-[11px] text-slate-500">
                  Must match the secret you paste into GitHub's webhook form. HMAC-SHA256 is
                  used to verify every incoming push.
                </p>
              </div>
              <div>
                <Label htmlFor="webhook-org">GitHub org (optional)</Label>
                <Input
                  id="webhook-org"
                  value={orgInput}
                  onChange={(e) => setOrgInput(e.target.value)}
                  placeholder="example-org"
                />
                <p className="mt-1 text-[11px] text-slate-500">
                  If set, only pushes coming from this GitHub org's repos will be honored.
                </p>
              </div>
              <div className="flex gap-2">
                <Button type="submit" disabled={submitting || (!secret && !status?.configured)}>
                  {submitting ? <><Spinner /> Saving…</> : "Save"}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => {
                    setEditing(false);
                    setSecret("");
                    setOrgInput(status?.github_org ?? "");
                  }}
                >
                  Cancel
                </Button>
              </div>
            </form>
          ) : (
            <div className="flex flex-wrap gap-2">
              <Button type="button" onClick={() => setEditing(true)}>
                {status?.configured ? "Replace secret / edit org" : "Configure"}
              </Button>
            </div>
          )}

          <details className="rounded-md border border-slate-200 bg-slate-50/60 p-3 text-xs dark:border-slate-800 dark:bg-slate-900/40">
            <summary className="cursor-pointer font-medium text-slate-700 dark:text-slate-300">
              Setup checklist
            </summary>
            <ol className="ml-4 mt-2 list-decimal space-y-1 text-slate-600 dark:text-slate-400">
              <li>Save a secret here (or paste an existing one).</li>
              <li>
                Open your repo in GitHub → <em>Settings → Webhooks → Add webhook</em>.
              </li>
              <li>
                Payload URL = the <strong>Webhook URL</strong> shown above. Content type =
                <code className="ml-1 font-mono">application/json</code>. Secret = the one you saved here.
              </li>
              <li>Events: select "Just the push event".</li>
              <li>
                In Dashboard → expand a workspace → enable <strong>auto-trigger on push</strong>.
                A push to that workspace's tracked branch will now start a plan.
              </li>
            </ol>
          </details>
        </CardBody>
      </Card>
    </div>
  );
}

function ModulesSection() {
  const [cfg, setCfg] = useState<ModulesConfig>({
    mode: "github",
    upstream_url: "",
    local_host_dir: "",
  });
  const [inherited, setInherited] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  async function load() {
    try {
      const r = await api.get("/v1/integrations/modules");
      setCfg({
        mode: (r.data?.mode === "local" ? "local" : "github"),
        upstream_url: r.data?.upstream_url ?? "",
        local_host_dir: r.data?.local_host_dir ?? "",
      });
      setInherited(!!r.data?.inherited);
    } catch (e: any) {
      // 404 is fine — no config yet
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { load(); }, []);

  async function save(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      await api.put("/v1/integrations/modules", cfg);
      setSaved("Saved");
      setTimeout(() => setSaved(null), 2000);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <ScopeBadge />
    <Card>
      <CardHeader>
        <CardTitle>Terraform modules registry</CardTitle>
        <div className="flex items-center gap-2">
          {inherited && <Badge tone="warning">inherited</Badge>}
          <Badge tone={cfg.mode === "github" ? "info" : "violet"}>{cfg.mode}</Badge>
        </div>
      </CardHeader>
      <CardBody>
        {inherited && (
          <div className="mb-4 rounded-md border border-amber-300/50 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200">
            Inherited from the legacy global config. Saving here writes a
            BU-scoped value.
          </div>
        )}
        <p className="mb-4 text-sm text-slate-500 dark:text-slate-400">
          Where to fetch the terraform modules referenced by{" "}
          <code className="font-mono text-xs">module &quot;x&quot; &#123; source = &quot;...&quot; &#125;</code>{" "}
          in your stacks. <strong>GitHub</strong> uses the configured GitHub
          token to clone the modules repo over HTTPS. <strong>Local</strong>{" "}
          rewrites that URL to a host-mounted checkout so you can develop
          against in-flight module changes without pushing.
        </p>

        {loading ? (
          <p className="text-sm italic text-slate-500">Loading…</p>
        ) : (
          <form onSubmit={save} className="space-y-4">
            <div className="inline-flex rounded-md border border-slate-200 p-0.5 dark:border-slate-700/70">
              {(["github", "local"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setCfg((p) => ({ ...p, mode: m }))}
                  className={cx(
                    "rounded px-3 py-1 text-xs font-medium capitalize transition-colors",
                    cfg.mode === m
                      ? "bg-sky-500 text-white"
                      : "text-slate-600 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800",
                  )}
                >
                  {m}
                </button>
              ))}
            </div>

            <div>
              <Label>Upstream URL (as referenced in `source =`)</Label>
              <Input
                value={cfg.upstream_url}
                onChange={(e) => setCfg((p) => ({ ...p, upstream_url: e.target.value }))}
                placeholder="https://github.com/your-org/terraform-modules.git"
              />
              <p className="mt-1 text-xs text-slate-500">
                The literal URL that appears after <code className="font-mono">source = &quot;git::</code> in your terraform code.
              </p>
            </div>

            {cfg.mode === "local" && (
              <div>
                <Label>Local host path</Label>
                <Input
                  value={cfg.local_host_dir}
                  onChange={(e) => setCfg((p) => ({ ...p, local_host_dir: e.target.value }))}
                  placeholder="/home/you/github/terraform-modules"
                />
                <p className="mt-1 text-xs text-slate-500">
                  Must live under the host&apos;s <code className="font-mono">TERRADUCKTEL_LOCAL_REPOS_HOST_DIR</code>. The executor will bind-mount this path read-only and rewrite the upstream URL to <code className="font-mono">file://</code>.
                </p>
              </div>
            )}

            {error && (
              <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">{error}</div>
            )}
            {saved && (
              <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-300">✓ {saved}</div>
            )}

            <div className="flex gap-2">
              <Button type="submit" disabled={saving}>
                {saving ? <><Spinner /> Saving…</> : "Save"}
              </Button>
            </div>
          </form>
        )}
      </CardBody>
    </Card>
    </div>
  );
}

// ─── infracost (cost estimation) ───────────────────────────────────────────

type InfracostStatus = {
  configured: boolean;
  api_key_tail?: string | null;
  currency: string;
  overridden_by_env?: boolean;
  inherited?: boolean;
};

function InfracostSection() {
  const [status, setStatus] = useState<InfracostStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [keyInput, setKeyInput] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [submitting, setSubmitting] = useState(false);
  const [test, setTest] = useState<{ ok: boolean; detail?: string; organization?: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);

  async function load() {
    try {
      const r = await api.get("/v1/integrations/infracost");
      setStatus(r.data);
      setCurrency(r.data.currency || "USD");
      setError(null);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Failed to load");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { load(); }, []);

  async function save(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const body: any = { currency };
      if (keyInput) body.api_key = keyInput;
      await api.put("/v1/integrations/infracost", body);
      setKeyInput("");
      setEditing(false);
      await load();
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Save failed");
    } finally {
      setSubmitting(false);
    }
  }

  async function remove() {
    setSubmitting(true);
    setError(null);
    try {
      await api.put("/v1/integrations/infracost", { api_key: "" });
      setConfirmRemove(false);
      await load();
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Remove failed");
    } finally {
      setSubmitting(false);
    }
  }

  async function runTest() {
    setTest(null);
    try {
      const r = await api.post("/v1/integrations/infracost/test");
      setTest(r.data);
    } catch (e: any) {
      setTest({ ok: false, detail: e?.response?.data?.detail ?? e?.message ?? "Test failed" });
    }
  }

  return (
    <div>
      <ScopeBadge />
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden className="text-emerald-600 dark:text-emerald-400">
            <path d="M11 21V11h2v10h-2Zm-5 0V11h2v10H6Zm10 0V11h2v10h-2ZM3 9 12 3l9 6v2H3V9Z" />
          </svg>
          <CardTitle>Infracost (cost estimation)</CardTitle>
        </div>
        {status?.configured && !status.overridden_by_env && !status.inherited && <Badge tone="success">configured</Badge>}
        {status?.configured && status.inherited && <Badge tone="warning">inherited</Badge>}
        {status?.overridden_by_env && <Badge tone="warning">env override</Badge>}
        {!status?.configured && !status?.overridden_by_env && <Badge tone="neutral">not set</Badge>}
      </CardHeader>
      <CardBody className="space-y-4">
        {status?.inherited && (
          <div className="rounded-md border border-amber-300/50 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200">
            Inherited from the legacy global config. Saving here writes a
            BU-scoped value.
          </div>
        )}
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Estimates the monthly USD cost of every plan. The value shows up on
          the <strong>Cost Estimation</strong> step in the run timeline so
          approvers can sanity-check spend before applying.{" "}
          <a href="https://www.infracost.io/docs/" target="_blank" rel="noreferrer noopener" className="text-sky-600 underline-offset-2 hover:underline dark:text-sky-400">
            Get a free API key →
          </a>
        </p>

        {status?.overridden_by_env && (
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-300">
            <code className="font-mono">INFRACOST_API_KEY</code> is set in the API container's environment, which overrides whatever's saved here.
          </div>
        )}

        {loading ? (
          <p className="text-sm italic text-slate-500">Loading…</p>
        ) : (
          <>
            {status?.configured && (
              <Field label="API key" value={<span className="font-mono">{status.api_key_tail ?? "configured"}</span>} />
            )}
            <Field label="Currency" value={status?.currency ?? "USD"} />
          </>
        )}

        {test && (
          <div className={"rounded-md border px-3 py-2 text-xs " + (test.ok ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-300" : "border-red-200 bg-red-50 text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300")}>
            {test.ok ? <p>✓ Authenticated{test.organization ? ` as ${test.organization}` : ""}</p> : <p>✕ {test.detail ?? "Test failed"}</p>}
          </div>
        )}

        {error && <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">{error}</div>}

        {editing ? (
          <form onSubmit={save} className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-[1fr_120px]">
              <div>
                <Label htmlFor="ic-key">API key</Label>
                <Input id="ic-key" type="password" value={keyInput} onChange={(e) => setKeyInput(e.target.value)} placeholder="ico-…" autoComplete="off" required />
              </div>
              <div>
                <Label htmlFor="ic-cur">Currency</Label>
                <Input id="ic-cur" value={currency} onChange={(e) => setCurrency(e.target.value)} placeholder="USD" />
              </div>
            </div>
            <div className="flex gap-2">
              <Button type="submit" disabled={submitting || !keyInput}>{submitting ? <><Spinner /> Saving…</> : "Save"}</Button>
              <Button type="button" variant="ghost" onClick={() => { setEditing(false); setKeyInput(""); }}>Cancel</Button>
            </div>
          </form>
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button type="button" onClick={() => setEditing(true)}>{status?.configured ? "Replace key" : "Add API key"}</Button>
            {status?.configured && <Button type="button" variant="secondary" onClick={runTest}>Test connection</Button>}
            {status?.configured && (
              <Button type="button" variant="ghost" className="ml-auto text-red-500 hover:text-red-400" onClick={() => setConfirmRemove(true)}>Remove</Button>
            )}
          </div>
        )}
      </CardBody>
    </Card>
    <ConfirmDialog
      open={confirmRemove}
      tone="danger"
      title="Remove Infracost API key"
      message={<>Remove the saved Infracost API key? Future runs will skip the Cost Estimation step.</>}
      confirmLabel="Remove"
      busy={submitting}
      onConfirm={remove}
      onCancel={() => setConfirmRemove(false)}
    />
    </div>
  );
}

// ─── Slack (bot token + channel) ───────────────────────────────────────────

type SlackStatus = {
  configured: boolean;
  token_tail?: string | null;
  team_name?: string | null;
  channel_id?: string | null;
  channel_name?: string | null;
};

type SlackChannel = { id: string; name: string; is_private?: boolean };

type SlackTestResult = {
  ok: boolean;
  detail?: string;
  team?: string;
  bot_user_id?: string;
  url?: string;
};

function SlackSection() {
  const [status, setStatus] = useState<SlackStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [tokenInput, setTokenInput] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [test, setTest] = useState<SlackTestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [channels, setChannels] = useState<SlackChannel[] | null>(null);
  const [channelId, setChannelId] = useState<string>("");
  const [loadingChannels, setLoadingChannels] = useState(false);
  const [channelKind, setChannelKind] = useState<"all" | "public" | "private">("all");
  const [channelSearch, setChannelSearch] = useState("");
  const [confirmRemove, setConfirmRemove] = useState(false);

  async function load() {
    try {
      const r = await api.get("/v1/integrations/slack");
      setStatus(r.data);
      setChannelId(r.data?.channel_id ?? "");
      setError(null);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Failed to load");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!tokenInput && !status?.configured) return;
    setSubmitting(true);
    setError(null);
    setTest(null);
    try {
      // Save token only — channel selection happens after verify, via saveChannel.
      await api.put("/v1/integrations/slack", { token: tokenInput });
      setTokenInput("");
      setEditing(false);
      await load();
      // Auto-load channel list after a successful token save so the operator
      // can pick the destination without a second click.
      await loadChannels();
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Save failed");
    } finally {
      setSubmitting(false);
    }
  }

  async function loadChannels() {
    setLoadingChannels(true);
    try {
      const r = await api.get("/v1/integrations/slack/channels");
      setChannels(r.data);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Failed to list channels");
    } finally {
      setLoadingChannels(false);
    }
  }

  async function saveChannel() {
    if (!channelId) return;
    const ch = channels?.find((c) => c.id === channelId);
    setSubmitting(true);
    setError(null);
    try {
      await api.put("/v1/integrations/slack", {
        channel_id: channelId,
        channel_name: ch?.name ?? null,
      });
      await load();
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Save failed");
    } finally {
      setSubmitting(false);
    }
  }

  async function remove() {
    setSubmitting(true);
    setError(null);
    try {
      await api.delete("/v1/integrations/slack");
      setChannels(null);
      setChannelId("");
      setConfirmRemove(false);
      await load();
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Remove failed");
    } finally {
      setSubmitting(false);
    }
  }

  async function runTest() {
    setTesting(true);
    setTest(null);
    try {
      const r = await api.post("/v1/integrations/slack/test");
      setTest(r.data);
    } catch (e: any) {
      setTest({ ok: false, detail: e?.response?.data?.detail ?? e?.message ?? "Test failed" });
    } finally {
      setTesting(false);
    }
  }

  return (
    <div>
      <ScopeBadge />
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden className="text-emerald-600 dark:text-emerald-400">
              {ICON.slack}
            </svg>
            <CardTitle>Slack notifications</CardTitle>
          </div>
          {status?.configured && status.channel_id && <Badge tone="success">configured</Badge>}
          {status?.configured && !status.channel_id && <Badge tone="warning">pick a channel</Badge>}
          {!status?.configured && <Badge tone="neutral">not set</Badge>}
        </CardHeader>
        <CardBody className="space-y-4">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Bot token + a channel (public or private) for run + drift notifications. We post on{" "}
            <strong>auto-approved (0/0/0)</strong>, <strong>awaiting approval</strong>,{" "}
            <strong>run failed</strong>, and <strong>drift detected</strong>. Token is
            encrypted at rest. The bot needs <code className="font-mono text-xs">chat:write</code>{" "}
            plus <code className="font-mono text-xs">channels:read</code> (for public channels)
            and/or <code className="font-mono text-xs">groups:read</code> (for private channels).
          </p>

          {loading ? (
            <p className="text-sm italic text-slate-500">Loading…</p>
          ) : status?.configured ? (
            <div className="grid gap-2 sm:grid-cols-2">
              <Field label="Token" value={<span className="font-mono">{status.token_tail ?? "configured"}</span>} />
              <Field label="Workspace" value={status.team_name ?? "—"} />
              <Field
                label="Channel"
                value={
                  status.channel_id ? (
                    <span>
                      {status.channel_name ? <strong>#{status.channel_name}</strong> : null}{" "}
                      <span className="font-mono text-xs text-slate-500">{status.channel_id}</span>
                    </span>
                  ) : (
                    <span className="text-amber-600">not picked yet</span>
                  )
                }
              />
            </div>
          ) : null}

          {test && (
            <div
              className={
                "rounded-md border px-3 py-2 text-xs " +
                (test.ok
                  ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-300"
                  : "border-red-200 bg-red-50 text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300")
              }
            >
              {test.ok ? (
                <p>
                  ✓ Connected to <strong>{test.team}</strong>
                  {test.bot_user_id && (
                    <> · bot <code className="font-mono">{test.bot_user_id}</code></>
                  )}
                </p>
              ) : (
                <p>✕ {test.detail ?? "Test failed"}</p>
              )}
            </div>
          )}

          {error && (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
              {error}
            </div>
          )}

          {editing ? (
            <form onSubmit={save} className="space-y-3">
              <div>
                <Label htmlFor="slack-token">Bot token</Label>
                <Input
                  id="slack-token"
                  type="password"
                  value={tokenInput}
                  onChange={(e) => setTokenInput(e.target.value)}
                  placeholder="xoxb-…"
                  autoComplete="off"
                  required
                />
                <p className="mt-1 text-[11px] text-slate-500">
                  Saving verifies the token against <code className="font-mono">auth.test</code> —
                  rejected tokens never get persisted.
                </p>
              </div>
              <div className="flex gap-2">
                <Button type="submit" disabled={submitting || !tokenInput}>
                  {submitting ? <><Spinner /> Verifying…</> : "Save token"}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => {
                    setEditing(false);
                    setTokenInput("");
                  }}
                >
                  Cancel
                </Button>
              </div>
            </form>
          ) : (
            <div className="flex flex-wrap gap-2">
              <Button type="button" onClick={() => setEditing(true)}>
                {status?.configured ? "Replace token" : "Add token"}
              </Button>
              {status?.configured && (
                <Button type="button" variant="secondary" onClick={runTest} disabled={testing}>
                  {testing ? <><Spinner /> Testing…</> : "Test connection"}
                </Button>
              )}
              {status?.configured && (
                <Button
                  type="button"
                  variant="ghost"
                  className="ml-auto text-red-500 hover:text-red-400"
                  onClick={() => setConfirmRemove(true)}
                  disabled={submitting}
                >
                  Remove
                </Button>
              )}
            </div>
          )}

          {status?.configured && (
            <div className="rounded-md border border-slate-200 bg-slate-50/60 p-3 dark:border-slate-800 dark:bg-slate-900/40">
              <div className="mb-2 flex items-center justify-between gap-3">
                <span className="text-xs font-medium text-slate-700 dark:text-slate-300">
                  Notification channel
                </span>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={loadChannels}
                  disabled={loadingChannels}
                >
                  {loadingChannels ? <><Spinner /> Loading…</> : channels ? "Refresh list" : "Load channels"}
                </Button>
              </div>
              {channels ? (
                (() => {
                  // Client-side filter + search. The full list is already in
                  // memory after Load; toggling kind / typing in the search
                  // box doesn't re-hit Slack.
                  const q = channelSearch.trim().toLowerCase();
                  const filtered = channels
                    .filter((c) => {
                      if (channelKind === "public" && c.is_private) return false;
                      if (channelKind === "private" && !c.is_private) return false;
                      return true;
                    })
                    .filter((c) => (q ? c.name.toLowerCase().includes(q) : true))
                    .slice()
                    .sort((a, b) => a.name.localeCompare(b.name));
                  const publicCount = channels.filter((c) => !c.is_private).length;
                  const privateCount = channels.length - publicCount;
                  return (
                    <div className="space-y-2">
                      <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
                        <span>
                          {channels.length} total · {publicCount} public · {privateCount} private
                        </span>
                        <span className="ml-auto inline-flex overflow-hidden rounded-md border border-slate-300 dark:border-slate-700">
                          {(["all", "public", "private"] as const).map((k) => (
                            <button
                              key={k}
                              type="button"
                              onClick={() => setChannelKind(k)}
                              className={cx(
                                "px-2 py-0.5 text-[11px] font-medium transition-colors",
                                channelKind === k
                                  ? "bg-slate-200 text-slate-900 dark:bg-slate-700 dark:text-slate-100"
                                  : "bg-white text-slate-600 hover:bg-slate-50 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800",
                              )}
                            >
                              {k}
                            </button>
                          ))}
                        </span>
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <input
                          value={channelSearch}
                          onChange={(e) => setChannelSearch(e.target.value)}
                          placeholder="Filter by name…"
                          className="w-44 rounded-md border border-slate-300 bg-white px-2.5 py-1 text-sm dark:border-slate-700 dark:bg-slate-950"
                        />
                        <select
                          value={channelId}
                          onChange={(e) => setChannelId(e.target.value)}
                          className="min-w-[14rem] rounded-md border border-slate-300 bg-white px-2.5 py-1 text-sm dark:border-slate-700 dark:bg-slate-950"
                        >
                          <option value="">
                            — select a channel ({filtered.length} shown) —
                          </option>
                          {filtered.map((c) => (
                            <option key={c.id} value={c.id}>
                              {c.is_private ? "🔒 " : "#"}
                              {c.name}
                            </option>
                          ))}
                        </select>
                        <Button
                          type="button"
                          size="sm"
                          onClick={saveChannel}
                          disabled={!channelId || submitting || channelId === (status.channel_id ?? "")}
                        >
                          {submitting ? <><Spinner /> Saving…</> : "Save channel"}
                        </Button>
                      </div>
                    </div>
                  );
                })()
              ) : (
                <p className="text-xs text-slate-500">
                  Click <strong>Load channels</strong> to fetch the list of channels the bot can
                  see (public + private). Private channels show with a 🔒 badge and only appear
                  if the bot was invited and the token carries <code className="font-mono text-[10px]">groups:read</code>.
                </p>
              )}
            </div>
          )}
        </CardBody>
      </Card>
      <ConfirmDialog
        open={confirmRemove}
        tone="danger"
        title="Remove Slack integration"
        message={
          <>
            Remove the Slack integration? Future run / drift notifications will stop until a new
            token is set.
          </>
        }
        confirmLabel="Remove"
        busy={submitting}
        onConfirm={remove}
        onCancel={() => setConfirmRemove(false)}
      />
    </div>
  );
}

// ─── security (checkov gate) ───────────────────────────────────────────────

function SecuritySection() {
  const [mode, setMode] = useState<"fail" | "warn">("fail");
  const [inherited, setInherited] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      const r = await api.get("/v1/integrations/checkov");
      setMode(r.data.mode === "warn" ? "warn" : "fail");
      setInherited(!!r.data.inherited);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Failed to load");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { load(); }, []);

  async function save(next: "fail" | "warn") {
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      await api.put("/v1/integrations/checkov", { mode: next });
      setMode(next);
      setInherited(false);
      setSaved(`Mode set to "${next}"`);
      setTimeout(() => setSaved(null), 2000);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <ScopeBadge />
    <Card>
      <CardHeader>
        <CardTitle>Checkov security gate</CardTitle>
        <div className="flex items-center gap-2">
          {inherited && <Badge tone="warning">inherited</Badge>}
          <Badge tone={mode === "fail" ? "danger" : "warning"}>{mode}</Badge>
        </div>
      </CardHeader>
      <CardBody className="space-y-4">
        {inherited && (
          <div className="rounded-md border border-amber-300/50 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200">
            Inherited from the legacy global config. Picking a mode here writes
            a BU-scoped value.
          </div>
        )}
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Before <code className="font-mono text-xs">terraform plan</code> runs, the executor scans
          your code with <a href="https://www.checkov.io/" target="_blank" rel="noreferrer noopener" className="text-sky-600 underline-offset-2 hover:underline dark:text-sky-400">Checkov</a> for known
          security misconfigurations.
        </p>

        {loading ? (
          <p className="text-sm italic text-slate-500">Loading…</p>
        ) : (
          <div className="space-y-3">
            <button
              type="button"
              onClick={() => save("fail")}
              disabled={saving || mode === "fail"}
              className={cx(
                "w-full rounded-lg border p-4 text-left transition-colors",
                mode === "fail"
                  ? "border-red-500 bg-red-50 dark:bg-red-950/30 dark:border-red-800"
                  : "border-slate-200 hover:bg-slate-50 dark:border-slate-700/70 dark:hover:bg-slate-800/40",
              )}
            >
              <div className="flex items-center gap-2">
                <Badge tone="danger">fail</Badge>
                <span className="font-medium">Block runs on any violation</span>
              </div>
              <p className="mt-1 text-xs text-slate-500">
                Strict mode — recommended for production. The run is marked
                failed and Terraform Plan never executes.
              </p>
            </button>
            <button
              type="button"
              onClick={() => save("warn")}
              disabled={saving || mode === "warn"}
              className={cx(
                "w-full rounded-lg border p-4 text-left transition-colors",
                mode === "warn"
                  ? "border-amber-500 bg-amber-50 dark:bg-amber-950/30 dark:border-amber-800"
                  : "border-slate-200 hover:bg-slate-50 dark:border-slate-700/70 dark:hover:bg-slate-800/40",
              )}
            >
              <div className="flex items-center gap-2">
                <Badge tone="warning">warn</Badge>
                <span className="font-medium">Capture violations, continue the run</span>
              </div>
              <p className="mt-1 text-xs text-slate-500">
                Findings show up as the Checkov step's output and the run
                proceeds to Terraform Plan. Useful for onboarding existing
                infra.
              </p>
            </button>
          </div>
        )}

        {error && (
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">{error}</div>
        )}
        {saved && (
          <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-300">✓ {saved}</div>
        )}
      </CardBody>
    </Card>
    </div>
  );
}

// ─── about section ─────────────────────────────────────────────────────────

// One short, plain-English explanation per tunable. Keep these short — the
// tooltip shows on hover next to the key. If you add a new key in
// `app/services/runtime_settings.py`, add a description here too; missing
// entries render the generic fallback.
const TUNABLE_DOCS: Record<string, string> = {
  "worker.poll_interval_seconds":
    "How often (seconds) the in-process job worker checks `run_jobs` for queued executor launches. Lower = lower run-trigger latency at the cost of more idle DB queries. Default 2.0s.",
  "worker.stale_after_seconds":
    "How long (seconds) without a heartbeat from an executor before the reaper marks the run failed and releases the workspace state-lock. Default 90s.",
  "worker.reaper_interval_seconds":
    "How often (seconds) the stale-run reaper sweeps. Shorter = faster recovery from wedged executors; longer = less DB chatter. Default 30s.",
  "drift.interval_seconds":
    "How often (seconds) the drift-detector loop runs a read-only `terraform plan` against every workspace. Default 300s (5 min).",
  "liveness.interval_seconds":
    "How often (seconds) the liveness detector checks core services. Default 300s.",
  "liveness.grace_seconds_after_create":
    "Quiet period (seconds) after a workspace is created before the liveness detector starts checking it — gives slow first-time `terraform init` time to finish. Default 600s.",
  "audit.verify_limit_rows":
    "Maximum number of audit-log rows `/api/v1/audit/verify` will walk in one pass. Higher = more complete verification but slower endpoint. Default 10000.",
  "auth.access_token_expire_minutes":
    "How long (minutes) a user's access JWT is valid after login or SSO callback. Existing tokens keep their original lifetime; changes apply on next login. Internal machine tokens are unaffected. Default 480 (8h).",
  "auth.refresh_token_expire_hours":
    "How long (hours) a user's refresh JWT is valid. Tokens already issued keep their original lifetime. Default 24.",
};


function InfoIcon({ tip }: { tip: string }) {
  // Inline tooltip via the native `title` attribute — keyboard-accessible,
  // works without a portal or a third-party tooltip library. Good enough for
  // a settings page; switch to a positioned popover if we ever need rich
  // formatting in the body.
  return (
    <span
      tabIndex={0}
      role="img"
      aria-label={tip}
      title={tip}
      className="inline-flex h-4 w-4 cursor-help items-center justify-center rounded-full border border-brand-border text-[10px] font-semibold text-brand-muted hover:border-brand-400 hover:text-brand-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-400"
    >
      i
    </span>
  );
}


function RuntimeTunablesSection() {
  type Setting = { value: number; default: number };
  const [data, setData] = useState<Record<string, Setting> | null>(null);
  const [editing, setEditing] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  async function load() {
    setErr(null);
    try {
      const res = await api.get("/v1/runtime-config");
      setData(res.data);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? "load failed");
    }
  }
  useEffect(() => {
    void load();
  }, []);

  async function save(key: string) {
    const raw = editing[key];
    if (raw === undefined) return;
    const value = Number(raw);
    if (!Number.isFinite(value) || value <= 0) {
      setErr(`${key}: value must be a positive number`);
      return;
    }
    setBusy(key);
    setErr(null);
    setOk(null);
    try {
      await api.put(`/v1/runtime-config/${key}`, { value });
      setOk(`${key} saved`);
      setEditing((e) => {
        const next = { ...e };
        delete next[key];
        return next;
      });
      await load();
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? "save failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Runtime tunables</CardTitle>
      </CardHeader>
      <CardBody className="space-y-4">
        <p className="text-sm text-brand-textSoft dark:text-brand-100/70">
          Worker poll/heartbeat intervals and detector cadences. Changes take effect
          within ~60s (config-cache TTL).
        </p>
        {err && <p className="text-sm text-red-600 dark:text-red-400">{err}</p>}
        {ok && <p className="text-sm text-emerald-700 dark:text-emerald-300">{ok}</p>}
        {data === null ? (
          <div className="flex items-center gap-2 text-sm text-slate-500"><Spinner /> Loading…</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wider text-brand-muted">
              <tr>
                <th className="py-2 font-medium">Key</th>
                <th className="py-2 font-medium">Active</th>
                <th className="py-2 font-medium">Default</th>
                <th className="py-2 font-medium">Update</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(data).map(([key, s]) => (
                <tr key={key} className="border-t border-brand-border">
                  <td className="py-2 pr-3 font-mono text-[12px]">
                    <span className="inline-flex items-center gap-1.5">
                      {key}
                      <InfoIcon
                        tip={TUNABLE_DOCS[key] ?? "Runtime tunable. No description available — see app/services/runtime_settings.py."}
                      />
                    </span>
                  </td>
                  <td className="py-2 pr-3 tabular-nums">{s.value}</td>
                  <td className="py-2 pr-3 tabular-nums text-brand-muted">{s.default}</td>
                  <td className="py-2 pr-3">
                    <div className="flex items-center gap-2">
                      <Input
                        className="w-28"
                        placeholder={String(s.value)}
                        value={editing[key] ?? ""}
                        onChange={(e) => setEditing((p) => ({ ...p, [key]: e.target.value }))}
                      />
                      <Button
                        size="sm"
                        variant="primary"
                        onClick={() => save(key)}
                        disabled={busy === key || (editing[key] ?? "") === ""}
                      >
                        {busy === key ? "…" : "Save"}
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardBody>
    </Card>
  );
}


function AboutSection() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>About Terraducktel</CardTitle>
      </CardHeader>
      <CardBody className="space-y-2 text-sm text-slate-500 dark:text-slate-400">
        <p>Self-hosted Terraform orchestration. Plans and applies are scoped per workspace, gated by operator approval, and audited end-to-end.</p>
        <p>
          State is stored per AWS account in S3, keyed by{" "}
          <span className="font-mono text-slate-600 dark:text-slate-500">
            &lt;account-bucket&gt;/&lt;tf_working_dir&gt;/terraform.tfstate
          </span>
          .
        </p>
      </CardBody>
    </Card>
  );
}

// ─── changelog (GitHub Releases of the TDT repo) ────────────────────────────

type ChangelogEntry = {
  id: string;
  source: "github" | "manual" | string;
  ref?: string | null;
  title: string;
  body?: string | null;
  author?: string | null;
  url?: string | null;
  entry_date?: string | null;
};

function ChangelogSection() {
  const user = useCurrentUser();
  const isAdmin = hasMinRole(user, "admin");
  const [entries, setEntries] = useState<ChangelogEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Admin: repo config + sync.
  const [repo, setRepo] = useState("");
  const [repoLoaded, setRepoLoaded] = useState(false);
  const [savingRepo, setSavingRepo] = useState(false);
  const [repoErr, setRepoErr] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);
  // Admin: manual add-entry form.
  const [showAdd, setShowAdd] = useState(false);
  const [draft, setDraft] = useState({ title: "", body: "", entry_date: "" });
  const [adding, setAdding] = useState(false);
  // Id of the entry pending delete-confirmation (null = no dialog open).
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  async function loadEntries() {
    setError(null);
    try {
      const r = await api.get("/v1/integrations/changelog/entries");
      setEntries(r.data);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Failed to load changelog");
      setEntries([]);
    }
  }

  async function loadRepo() {
    try {
      const r = await api.get("/v1/integrations/changelog");
      setRepo(r.data?.repo ?? "");
    } catch {
      // viewer or no config — leave blank
    } finally {
      setRepoLoaded(true);
    }
  }

  useEffect(() => {
    void loadEntries();
    if (isAdmin) void loadRepo();
    else setRepoLoaded(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function saveRepo(e: FormEvent) {
    e.preventDefault();
    setSavingRepo(true);
    setRepoErr(null);
    try {
      await api.put("/v1/integrations/changelog", { repo: repo.trim() });
      setRepoErr(null);
    } catch (e: any) {
      setRepoErr(e?.response?.data?.detail ?? e?.message ?? "Save failed");
    } finally {
      setSavingRepo(false);
    }
  }

  async function sync() {
    setSyncing(true);
    setSyncMsg(null);
    setError(null);
    try {
      const r = await api.post("/v1/integrations/changelog/sync");
      setSyncMsg(`Synced ${r.data?.synced ?? 0} pull request(s) from GitHub.`);
      await loadEntries();
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Sync failed");
    } finally {
      setSyncing(false);
    }
  }

  async function addEntry(e: FormEvent) {
    e.preventDefault();
    if (!draft.title.trim()) return;
    setAdding(true);
    setError(null);
    try {
      await api.post("/v1/integrations/changelog/entries", {
        title: draft.title.trim(),
        body: draft.body.trim() || undefined,
        entry_date: draft.entry_date || undefined,
      });
      setDraft({ title: "", body: "", entry_date: "" });
      setShowAdd(false);
      await loadEntries();
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Add failed");
    } finally {
      setAdding(false);
    }
  }

  async function deleteEntry() {
    if (!confirmDeleteId) return;
    setDeleting(true);
    setError(null);
    try {
      await api.delete(`/v1/integrations/changelog/entries/${confirmDeleteId}`);
      setConfirmDeleteId(null);
      await loadEntries();
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Delete failed");
    } finally {
      setDeleting(false);
    }
  }

  function fmtDate(iso?: string | null): string {
    if (!iso) return "";
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString();
  }

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader>
          <CardTitle>Changelog</CardTitle>
          <Button size="sm" variant="ghost" onClick={loadEntries}>
            Refresh
          </Button>
        </CardHeader>
        <CardBody className="space-y-4">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            What shipped in each deployment. Entries are stored in Terraducktel —
            {isAdmin ? " synced from the repo's merged pull requests, or added by hand." : " kept up to date by an admin."}
          </p>

          {isAdmin && repoLoaded && (
            <div className="space-y-3 rounded-md border border-slate-200 bg-slate-50/60 p-3 dark:border-slate-800 dark:bg-slate-900/40">
              <form onSubmit={saveRepo}>
                <Label>Source repo (owner/repo)</Label>
                <div className="mt-1 flex flex-wrap gap-2">
                  <Input
                    value={repo}
                    onChange={(e) => setRepo(e.target.value)}
                    placeholder="your-org/terraducktel"
                    className="max-w-xs"
                  />
                  <Button type="submit" variant="secondary" disabled={savingRepo}>
                    {savingRepo ? <><Spinner /> Saving…</> : "Save repo"}
                  </Button>
                  <Button type="button" onClick={sync} disabled={syncing}>
                    {syncing ? <><Spinner /> Syncing…</> : "↻ Sync from GitHub"}
                  </Button>
                  <Button type="button" variant="ghost" onClick={() => setShowAdd((v) => !v)}>
                    {showAdd ? "Cancel" : "+ Add entry"}
                  </Button>
                </div>
                <p className="mt-2 text-[11px] text-slate-500">
                  Sync pulls merged PRs into TDT (private repos need a GitHub token with read access — Settings → GitHub). The list below reads only from TDT.
                </p>
                {repoErr && <p className="mt-1 text-xs text-red-600 dark:text-red-400">{repoErr}</p>}
                {syncMsg && <p className="mt-1 text-xs text-emerald-700 dark:text-emerald-300">{syncMsg}</p>}
              </form>

              {showAdd && (
                <form onSubmit={addEntry} className="space-y-2 border-t border-slate-200 pt-3 dark:border-slate-800">
                  <Input
                    value={draft.title}
                    onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))}
                    placeholder="Title (e.g. Hotfix: drift detector cadence)"
                    required
                  />
                  <textarea
                    value={draft.body}
                    onChange={(e) => setDraft((d) => ({ ...d, body: e.target.value }))}
                    placeholder="Details (optional)"
                    rows={3}
                    className="w-full rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-950"
                  />
                  <div className="flex flex-wrap items-center gap-2">
                    <input
                      type="date"
                      value={draft.entry_date}
                      onChange={(e) => setDraft((d) => ({ ...d, entry_date: e.target.value }))}
                      className="rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-950"
                    />
                    <Button type="submit" disabled={adding || !draft.title.trim()}>
                      {adding ? <><Spinner /> Adding…</> : "Add entry"}
                    </Button>
                    <span className="text-[11px] text-slate-500">Date optional — defaults to today.</span>
                  </div>
                </form>
              )}
            </div>
          )}

          {error && (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
              {error}
            </div>
          )}

          {entries === null ? (
            <p className="text-sm italic text-slate-500">Loading…</p>
          ) : entries.length === 0 ? (
            <p className="text-sm italic text-slate-500">
              {isAdmin
                ? "No entries yet — Sync from GitHub or add one above."
                : "No changelog entries yet."}
            </p>
          ) : (
            <ol className="space-y-3">
              {entries.map((e) => (
                <li
                  key={e.id}
                  className="rounded-lg border border-slate-200 p-3 dark:border-slate-800/80"
                >
                  <div className="flex flex-wrap items-baseline gap-2">
                    {e.source === "github" && e.ref ? (
                      <span className="font-mono text-xs text-slate-500">#{e.ref}</span>
                    ) : (
                      <Badge tone="neutral">manual</Badge>
                    )}
                    <span className="text-sm font-medium text-slate-900 dark:text-slate-100">
                      {e.title}
                    </span>
                    {e.entry_date && (
                      <span className="ml-auto text-xs text-slate-500">{fmtDate(e.entry_date)}</span>
                    )}
                    {isAdmin && (
                      <button
                        type="button"
                        onClick={() => setConfirmDeleteId(e.id)}
                        title="Delete entry"
                        className="text-xs text-slate-400 hover:text-red-500"
                      >
                        ✕
                      </button>
                    )}
                  </div>
                  {e.body && e.body.trim() && (
                    <pre className="mt-2 whitespace-pre-wrap break-words font-sans text-[13px] leading-relaxed text-slate-600 dark:text-slate-300">
                      {e.body.trim()}
                    </pre>
                  )}
                  {e.author && (
                    <div className="mt-1 text-[11px] text-slate-500">by {e.author}</div>
                  )}
                </li>
              ))}
            </ol>
          )}
        </CardBody>
      </Card>
      <ConfirmDialog
        open={confirmDeleteId !== null}
        tone="danger"
        title="Delete changelog entry"
        message={<>Delete this changelog entry? This cannot be undone.</>}
        confirmLabel="Delete"
        busy={deleting}
        onConfirm={deleteEntry}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </div>
  );
}

// ─── settings shell with side nav ──────────────────────────────────────────

type Tab = {
  id: string;
  label: string;
  icon: JSX.Element;
  roleGate?: UserRole;
  render: () => JSX.Element;
};

const ICON = {
  account: <path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4Z" />,
  appearance: <path d="M12 3a9 9 0 1 0 0 18 1.5 1.5 0 0 0 1.06-2.56l-1.06-1.06A1.5 1.5 0 0 1 13.06 15h2.69A5.25 5.25 0 0 0 21 9.75C21 5.47 16.97 2 12 2v1Z" />,
  persistent: <path d="M12 1 3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4Zm0 6a3 3 0 1 1 0 6 3 3 0 0 1 0-6Zm0 13.93c-3-.93-5.95-4.55-6-9.93h12c-.05 5.38-3 9-6 9.93Z" />,
  github: <path d="M12 .3a12 12 0 0 0-3.79 23.4c.6.11.83-.26.83-.58v-2c-3.34.73-4.04-1.61-4.04-1.61-.55-1.39-1.34-1.76-1.34-1.76-1.09-.74.08-.73.08-.73 1.21.08 1.84 1.24 1.84 1.24 1.07 1.84 2.81 1.31 3.5 1 .11-.78.42-1.31.76-1.61-2.66-.3-5.46-1.33-5.46-5.93 0-1.31.47-2.38 1.24-3.22-.13-.3-.54-1.52.12-3.18 0 0 1.01-.32 3.3 1.23a11.5 11.5 0 0 1 6 0c2.29-1.55 3.3-1.23 3.3-1.23.66 1.66.25 2.88.12 3.18.77.84 1.24 1.91 1.24 3.22 0 4.61-2.81 5.62-5.48 5.92.43.37.81 1.1.81 2.22v3.29c0 .32.22.7.83.58A12 12 0 0 0 12 .3Z" />,
  modules: <path d="M21 16.5c0 .38-.21.71-.53.88l-7.9 4.44c-.16.12-.36.18-.57.18-.21 0-.41-.06-.57-.18l-7.9-4.44A.991.991 0 0 1 3 16.5v-9c0-.38.21-.71.53-.88L11.43 2.18c.16-.12.36-.18.57-.18.21 0 .41.06.57.18l7.9 4.44c.32.17.53.5.53.88v9ZM12 4.15 6.04 7.5 12 10.85l5.96-3.35L12 4.15ZM5 15.91l6 3.38v-6.71L5 9.21v6.7Zm14 0v-6.7l-6 3.37v6.71l6-3.38Z" />,
  variables: <path d="M4 4h16v4H4V4Zm0 6h10v4H4v-4Zm0 6h16v4H4v-4Z" />,
  security: <path d="M12 1 3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4Zm-1.4 14.6L7 12l1.4-1.4 2.2 2.2 5.8-5.8L17.8 8.4l-7.2 7.2Z" />,
  cost: <path d="M11.8 11c-2.27-.59-3-1.2-3-2.15 0-1.09 1.01-1.85 2.7-1.85 1.78 0 2.44.85 2.5 2.1h2.21c-.07-1.72-1.12-3.3-3.21-3.81V3h-3v2.16c-1.94.42-3.5 1.68-3.5 3.61 0 2.31 1.91 3.46 4.7 4.13 2.5.6 3 1.48 3 2.41 0 .69-.49 1.79-2.7 1.79-2.06 0-2.87-.92-2.98-2.1H6.32c.12 2.19 1.76 3.42 3.68 3.83V21h3v-2.15c1.95-.37 3.5-1.5 3.5-3.55 0-2.84-2.43-3.81-4.7-4.4Z" />,
  cloud: <path d="M19.35 10.04A7.49 7.49 0 0 0 12 4a7.5 7.5 0 0 0-6.94 4.66A6 6 0 0 0 6 20h13a5 5 0 0 0 .35-9.96Z" />,
  about: <path d="M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2Zm1 15h-2v-6h2Zm0-8h-2V7h2Z" />,
  changelog: <path d="M9 2a2 2 0 0 0-2 2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-2a2 2 0 0 0-2-2H9Zm0 2h6v2H9V4ZM7 10h10v2H7v-2Zm0 4h10v2H7v-2Z" />,
  slack: <path d="M5.04 15.16a2.13 2.13 0 1 1 0-4.26h2.13v2.13c0 1.18-.95 2.13-2.13 2.13Zm1.06-5.34A2.13 2.13 0 0 1 4 7.68 2.13 2.13 0 0 1 6.1 5.55a2.13 2.13 0 0 1 2.13 2.13v2.14H6.1Zm5.32 1.06a2.13 2.13 0 0 1-2.13-2.13 2.13 2.13 0 0 1 2.13-2.13 2.13 2.13 0 0 1 2.13 2.13v2.13h-2.13Zm0 7.46a2.13 2.13 0 0 1-2.13-2.13v-2.13h2.13a2.13 2.13 0 0 1 2.13 2.13c0 1.17-.95 2.13-2.13 2.13Zm5.32-7.46h-2.13V8.74a2.13 2.13 0 1 1 4.26 0 2.13 2.13 0 0 1-2.13 2.14Zm-1.06 1.07a2.13 2.13 0 1 1 0 4.26h-2.13v-2.13a2.13 2.13 0 0 1 2.13-2.13Z" />,
  webhook: <path d="M10.46 19a3.54 3.54 0 1 1-7.08 0 3.54 3.54 0 0 1 5.59-2.89l3.15-5.46a4.95 4.95 0 1 1 8.4-2.27h-2.05a2.97 2.97 0 1 0-5.27 1.83l-4.36 7.55a3.55 3.55 0 0 1 1.62 1.24Zm6.04-7.78a3.54 3.54 0 1 1-2.85 5.62l-6.3 0a4.97 4.97 0 0 1-9.35-2.34 4.95 4.95 0 0 1 1.99-3.98l1.18 1.7a2.97 2.97 0 1 0 4.42 3.04h8.04a3.54 3.54 0 0 1 2.87-4.04Z" />,
  apikeys: <path d="M14 6a5 5 0 1 0-4.9 6h.9l2 2 2-2h1l2-2-2-2h-3.1A5 5 0 0 0 14 6Zm-7 1a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3Z" />,
  policies: <path d="M12 1 3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4Zm0 5a2.5 2.5 0 0 1 2.5 2.5c0 1-.6 1.7-1.2 2.2-.5.4-.8.7-.8 1.3h-1c0-1 .5-1.6 1.1-2.1.5-.4.9-.7.9-1.4A1.5 1.5 0 0 0 12 7a1.5 1.5 0 0 0-1.5 1.5h-1A2.5 2.5 0 0 1 12 6Zm-.5 8h1v1h-1v-1Z" />,
};

function Icon({ d }: { d: JSX.Element }) {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden>{d}</svg>;
}

function LoggedInView() {
  const user = useCurrentUser();
  const allTabs: Tab[] = [
    { id: "account", label: "Account", icon: <Icon d={ICON.account} />, render: () => <AccountSection /> },
    { id: "appearance", label: "Appearance", icon: <Icon d={ICON.appearance} />, render: () => <AppearanceSection /> },
    { id: "persistent", label: "Persistent login", icon: <Icon d={ICON.persistent} />, render: () => <PersistentLoginSection /> },
    { id: "github", label: "GitHub", icon: <Icon d={ICON.github} />, roleGate: "admin" as UserRole, render: () => <div className="space-y-6"><GitHubSection /><InfraRepoSection /></div> },
    { id: "cloud", label: "Cloud resources", icon: <Icon d={ICON.cloud} />, roleGate: "admin" as UserRole, render: () => <CloudProviders /> },
    { id: "variables", label: "Variables", icon: <Icon d={ICON.variables} />, roleGate: "admin" as UserRole, render: () => <VariablesSection canWrite={hasMinRole(user, "admin")} /> },
    { id: "modules", label: "Terraform modules", icon: <Icon d={ICON.modules} />, roleGate: "admin" as UserRole, render: () => <ModulesSection /> },
    { id: "security", label: "Security", icon: <Icon d={ICON.security} />, roleGate: "admin" as UserRole, render: () => <SecuritySection /> },
    { id: "policies", label: "Policies", icon: <Icon d={ICON.policies} />, roleGate: "admin" as UserRole, render: () => <Suspense fallback={<p className="text-sm italic text-slate-500">Loading editor…</p>}><PoliciesSection /></Suspense> },
    { id: "cost", label: "Cost (Infracost)", icon: <Icon d={ICON.cost} />, roleGate: "admin" as UserRole, render: () => <InfracostSection /> },
    { id: "slack", label: "Slack", icon: <Icon d={ICON.slack} />, roleGate: "admin" as UserRole, render: () => <SlackSection /> },
    { id: "webhook", label: "Webhooks", icon: <Icon d={ICON.webhook} />, roleGate: "admin" as UserRole, render: () => <WebhookSection /> },
    { id: "api-keys", label: "API keys", icon: <Icon d={ICON.apikeys} />, roleGate: "admin" as UserRole, render: () => <ApiKeysSection /> },
    { id: "tunables", label: "Runtime tunables", icon: <Icon d={ICON.security} />, roleGate: "admin" as UserRole, render: () => <RuntimeTunablesSection /> },
    { id: "changelog", label: "Changelog", icon: <Icon d={ICON.changelog} />, render: () => <ChangelogSection /> },
    { id: "about", label: "About", icon: <Icon d={ICON.about} />, render: () => <AboutSection /> },
  ];
  const tabs: Tab[] = allTabs.filter((t) => !t.roleGate || hasMinRole(user, t.roleGate));

  const initial =
    typeof window !== "undefined" && window.location.hash
      ? window.location.hash.slice(1)
      : tabs[0].id;
  const [active, setActive] = useState(initial);
  useEffect(() => {
    const onHash = () => setActive(window.location.hash.slice(1) || tabs[0].id);
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function pick(id: string) {
    window.location.hash = id;
    setActive(id);
  }

  const current = tabs.find((t) => t.id === active) ?? tabs[0];

  return (
    <div>
      <SectionHeader eyebrow="Workspace" title="Settings" subtitle="Account, integrations, and session preferences." />
      <div className="grid gap-6 md:grid-cols-[200px_1fr]">
        <nav className="flex flex-row md:flex-col gap-1 md:sticky md:top-6 md:self-start overflow-x-auto md:overflow-visible">
          {tabs.map((t) => {
            const a = t.id === active;
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => pick(t.id)}
                className={cx(
                  "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors whitespace-nowrap",
                  a
                    ? "bg-slate-100 text-slate-900 dark:bg-slate-800/80 dark:text-slate-100"
                    : "text-slate-600 hover:bg-slate-50 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800/40 dark:hover:text-slate-200",
                )}
              >
                <span className={a ? "text-sky-600 dark:text-sky-400" : "text-slate-500"}>{t.icon}</span>
                {t.label}
              </button>
            );
          })}
        </nav>
        <div className="min-w-0">{current.render()}</div>
      </div>
    </div>
  );
}


export default function Settings() {
  return <LoggedInView />;
}
