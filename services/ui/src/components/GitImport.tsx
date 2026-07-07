import { FormEvent, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { Badge, Button, Card, CardBody, CardHeader, CardTitle, Input, Label, Spinner, cx } from "./ui";

type StackCandidate = {
  path: string;
  name: string;
  aws_account_id: string;
  region: string;
  suggested_environment: string;
  has_tf: boolean;
  // "terraform" (default) or "helm" — derived server-side from the on-disk
  // signal (*.tf vs Chart.yaml). Defaults to terraform for older payloads.
  kind?: string;
  already_imported?: boolean;
};

type DiscoveryAccount = {
  aws_account_id: string;
  regions: Record<string, StackCandidate[]>;
};

type DiscoveryResult = {
  repo_url: string;
  ref: string;
  accounts: DiscoveryAccount[];
  stack_count: number;
  errors: string[];
};

const ENVIRONMENTS = ["prod", "preprod", "staging", "dev", "shared"];

type SourceMode = "remote" | "local";

export default function GitImport({ onImported }: { onImported: () => void }) {
  const [mode, setMode] = useState<SourceMode>("remote");
  // Prefilled from the per-BU default base-infra repo URL (Settings → GitHub).
  // Empty until loaded / when unset — the Input falls back to its placeholder.
  const [repoUrl, setRepoUrl] = useState("");
  const [ref, setRef] = useState("main");
  const [username, setUsername] = useState("");
  const [token, setToken] = useState("");
  const [showCreds, setShowCreds] = useState(false);
  const [localPath, setLocalPath] = useState("/mnt/local-repos/terraform-infra");
  const [discovering, setDiscovering] = useState(false);
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState<DiscoveryResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [envOverrides, setEnvOverrides] = useState<Record<string, string>>({});
  const [importMsg, setImportMsg] = useState<string | null>(null);
  const [configuredAccounts, setConfiguredAccounts] = useState<Set<string>>(new Set());

  // Pre-load configured AWS accounts so we can flag rows that won't have a
  // working state backend until the admin adds them.
  useEffect(() => {
    api
      .get("/v1/aws-accounts")
      .then((r) => setConfiguredAccounts(new Set(r.data.map((a: any) => a.account_id))))
      .catch(() => setConfiguredAccounts(new Set()));
  }, []);

  // Prefill the repo URL from the BU's configured default base-infra repo
  // (Settings → GitHub). Leaves the field empty (→ placeholder) when unset.
  useEffect(() => {
    api
      .get("/v1/integrations/infra-repo")
      .then((r) => {
        const url = (r.data?.repo_url ?? "").trim();
        if (url) setRepoUrl(url);
      })
      .catch(() => {});
  }, []);

  async function onDiscover(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setImportMsg(null);
    setDiscovering(true);
    setResult(null);
    setSelected(new Set());
    setEnvOverrides({});
    try {
      const body =
        mode === "local"
          ? { local_path: localPath }
          : {
              repo_url: repoUrl,
              ref,
              username: username || undefined,
              token: token || undefined,
            };
      const r = await api.post("/v1/workspaces/discover", body);
      setResult(r.data);
      // Pre-select everything *except* what's already imported in this BU.
      // Re-importing them is a no-op on the backend (dedup) but it's misleading
      // for the dialog to ask, and "Select all" stays available if the operator
      // really wants to re-tick everything.
      const all = new Set<string>();
      for (const acc of r.data.accounts) {
        for (const region of Object.keys(acc.regions)) {
          for (const stack of acc.regions[region]) {
            if (!stack.already_imported) all.add(stack.path);
          }
        }
      }
      setSelected(all);
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? err?.message ?? "Discovery failed");
    } finally {
      setDiscovering(false);
    }
  }

  function toggle(path: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  function selectAllInRegion(stacks: StackCandidate[], add: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const s of stacks) {
        if (add) next.add(s.path);
        else next.delete(s.path);
      }
      return next;
    });
  }

  async function onImport() {
    if (!result) return;
    setImporting(true);
    setError(null);
    setImportMsg(null);
    try {
      const entries: any[] = [];
      for (const acc of result.accounts) {
        for (const region of Object.keys(acc.regions)) {
          for (const stack of acc.regions[region]) {
            if (!selected.has(stack.path)) continue;
            entries.push({
              path: stack.path,
              name: stack.name,
              aws_account_id: stack.aws_account_id,
              region: stack.region,
              environment: envOverrides[stack.path] ?? stack.suggested_environment,
            });
          }
        }
      }
      if (entries.length === 0) {
        setError("Pick at least one stack to import.");
        setImporting(false);
        return;
      }
      const r = await api.post("/v1/workspaces/import", {
        repo_url: result.repo_url,
        ref: result.ref,
        entries,
      });
      const created = r.data.created.length;
      const skipped = r.data.skipped.length;
      setImportMsg(
        `Imported ${created} workspace${created === 1 ? "" : "s"}` +
          (skipped ? ` (${skipped} already existed)` : "."),
      );
      onImported();
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? err?.message ?? "Import failed");
    } finally {
      setImporting(false);
    }
  }

  const selectedCount = selected.size;
  const totalStacks = useMemo(() => {
    if (!result) return 0;
    return result.accounts.reduce(
      (sum, a) => sum + Object.values(a.regions).reduce((s, r) => s + r.length, 0),
      0,
    );
  }, [result]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Import workspaces from Git</CardTitle>
        <span className="text-xs text-slate-500 dark:text-slate-500">
          Each <code className="font-mono">account-XXX/region/leaf</code> folder becomes a workspace with its own tfstate.
        </span>
      </CardHeader>
      <CardBody className="space-y-5">
        {/* Source mode toggle */}
        <div className="inline-flex rounded-md border border-slate-200 p-0.5 dark:border-slate-700/70">
          {(["remote", "local"] as SourceMode[]).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              className={cx(
                "rounded px-3 py-1 text-xs font-medium transition-colors",
                mode === m
                  ? "bg-sky-500 text-white"
                  : "text-slate-600 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800",
              )}
            >
              {m === "remote" ? "Remote URL" : "Local path"}
            </button>
          ))}
          {mode === "local" && (
            <span className="ml-3 self-center text-xs text-slate-500">
              dev only — uses the API container&apos;s mount at <code>/mnt/local-repos</code>
            </span>
          )}
        </div>

        {mode === "remote" ? (
          <form onSubmit={onDiscover} className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-[1fr_140px_auto]">
              <div>
                <Label>Repository URL</Label>
                <Input
                  value={repoUrl}
                  onChange={(e) => setRepoUrl(e.target.value)}
                  placeholder="https://github.com/org/terraform-infra.git"
                  required
                />
              </div>
              <div>
                <Label>Branch / ref</Label>
                <Input value={ref} onChange={(e) => setRef(e.target.value)} placeholder="main" />
              </div>
              <div className="flex items-end">
                <Button type="submit" disabled={discovering || !repoUrl}>
                  {discovering ? <><Spinner /> Scanning…</> : "Discover"}
                </Button>
              </div>
            </div>

            <div>
              <button
                type="button"
                onClick={() => setShowCreds((v) => !v)}
                className="text-xs text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"
              >
                {showCreds ? "▾" : "▸"} Private repo? Add username + token
              </button>
              {showCreds && (
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <div>
                    <Label>Username</Label>
                    <Input
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      placeholder="github-user"
                      autoComplete="off"
                    />
                  </div>
                  <div>
                    <Label>Personal access token</Label>
                    <Input
                      type="password"
                      value={token}
                      onChange={(e) => setToken(e.target.value)}
                      placeholder="ghp_… or fine-grained PAT"
                      autoComplete="off"
                    />
                  </div>
                  <p className="sm:col-span-2 text-xs text-slate-500 dark:text-slate-500">
                    One-shot only — used for this clone and discarded. Not persisted on the workspace.
                  </p>
                </div>
              )}
            </div>
          </form>
        ) : (
          <form onSubmit={onDiscover} className="grid gap-3 sm:grid-cols-[1fr_auto]">
            <div>
              <Label>Local path (inside API container)</Label>
              <Input
                value={localPath}
                onChange={(e) => setLocalPath(e.target.value)}
                placeholder="/mnt/local-repos/terraform-infra"
                required
              />
              <p className="mt-1 text-xs text-slate-500">
                Must live under the API&apos;s <code>TERRADUCKTEL_LOCAL_REPOS_DIR</code> mount.
                Set <code>TERRADUCKTEL_LOCAL_REPOS_HOST_DIR</code> in <code>.env</code> to your host path.
              </p>
            </div>
            <div className="flex items-end">
              <Button type="submit" disabled={discovering || !localPath}>
                {discovering ? <><Spinner /> Scanning…</> : "Scan"}
              </Button>
            </div>
          </form>
        )}

        {error && (
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
            {error}
          </div>
        )}
        {importMsg && (
          <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-300">
            {importMsg}
          </div>
        )}

        {result && (
          <>
            {result.errors.length > 0 && (
              <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-300">
                {result.errors.map((e, i) => (
                  <p key={i}>{e}</p>
                ))}
              </div>
            )}

            <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4 border-slate-200 dark:border-slate-800/80">
              <p className="text-sm text-slate-600 dark:text-slate-400">
                Found <strong>{totalStacks}</strong> stack{totalStacks === 1 ? "" : "s"} across{" "}
                {result.accounts.length} account{result.accounts.length === 1 ? "" : "s"}.{" "}
                <strong>{selectedCount}</strong> selected.
              </p>
              <div className="flex gap-2">
                <Button size="sm" variant="ghost" type="button" onClick={() => setSelected(new Set())}>
                  Select none
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  type="button"
                  onClick={() => {
                    const all = new Set<string>();
                    for (const a of result.accounts)
                      for (const r of Object.keys(a.regions))
                        for (const s of a.regions[r]) all.add(s.path);
                    setSelected(all);
                  }}
                >
                  Select all
                </Button>
              </div>
            </div>

            <div className="space-y-4 max-h-[60vh] overflow-y-auto pr-2">
              {result.accounts.map((acc) => {
                const isConfigured = configuredAccounts.has(acc.aws_account_id);
                return (
                <div key={acc.aws_account_id} className="rounded-lg border border-slate-200 dark:border-slate-800/80">
                  <div className="flex items-center justify-between border-b px-4 py-2.5 border-slate-200 bg-slate-50 dark:border-slate-800/80 dark:bg-slate-900/40">
                    <div className="flex items-center gap-2">
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" className="text-slate-400">
                        <path d="M3 6h18v12H3z" opacity=".15" />
                        <path d="M21 4H3a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h18a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2Zm0 14H3V8h18v10ZM3 6h18V6Z" />
                      </svg>
                      <span className="font-mono text-sm font-semibold">{acc.aws_account_id}</span>
                      {isConfigured ? (
                        <Badge tone="success">configured</Badge>
                      ) : (
                        <Badge tone="warning" className="ml-1">
                          <Link to="/aws" className="underline-offset-2 hover:underline">
                            no AWS account — add one
                          </Link>
                        </Badge>
                      )}
                    </div>
                    <span className="text-xs text-slate-500">
                      {Object.values(acc.regions).reduce((s, r) => s + r.length, 0)} stack(s)
                    </span>
                  </div>
                  {Object.keys(acc.regions).sort().map((region) => {
                    const stacks = acc.regions[region];
                    const allSelected = stacks.every((s) => selected.has(s.path));
                    const someSelected = stacks.some((s) => selected.has(s.path));
                    return (
                      <div key={region} className="border-t border-slate-200 dark:border-slate-800/80">
                        <div className="flex items-center justify-between px-4 py-2 bg-white dark:bg-slate-950/30">
                          <div className="flex items-center gap-2">
                            <input
                              type="checkbox"
                              checked={allSelected}
                              ref={(el) => {
                                if (el) el.indeterminate = !allSelected && someSelected;
                              }}
                              onChange={(e) => selectAllInRegion(stacks, e.target.checked)}
                              className="h-4 w-4 rounded border-slate-300 text-sky-600 focus:ring-sky-500 dark:border-slate-600 dark:bg-slate-900"
                            />
                            <span className="font-mono text-xs font-medium text-slate-600 dark:text-slate-400">{region}</span>
                          </div>
                          <span className="text-xs text-slate-500">{stacks.length}</span>
                        </div>
                        <ul>
                          {stacks.map((stack) => {
                            const isSelected = selected.has(stack.path);
                            const env = envOverrides[stack.path] ?? stack.suggested_environment;
                            const alreadyImported = !!stack.already_imported;
                            return (
                              <li
                                key={stack.path}
                                className={cx(
                                  "flex items-center gap-3 px-4 py-2 text-sm border-t border-slate-100 dark:border-slate-800/60",
                                  isSelected ? "bg-sky-50/50 dark:bg-sky-950/20" : "",
                                  alreadyImported && !isSelected ? "opacity-60" : "",
                                )}
                              >
                                <input
                                  type="checkbox"
                                  checked={isSelected}
                                  onChange={() => toggle(stack.path)}
                                  className="h-4 w-4 rounded border-slate-300 text-sky-600 focus:ring-sky-500 dark:border-slate-600 dark:bg-slate-900"
                                  title={alreadyImported ? "Already imported in this BU — re-importing is a no-op." : undefined}
                                />
                                <div className="min-w-0 flex-1">
                                  <p className="flex items-center gap-2 truncate font-medium">
                                    {stack.name}
                                    {alreadyImported && <Badge tone="neutral">already imported</Badge>}
                                  </p>
                                  <p className="truncate font-mono text-[11px] text-slate-500 dark:text-slate-500">{stack.path}</p>
                                </div>
                                <select
                                  value={env}
                                  onChange={(e) =>
                                    setEnvOverrides((prev) => ({ ...prev, [stack.path]: e.target.value }))
                                  }
                                  className="rounded-md border border-slate-300 bg-white px-2 py-1 text-xs dark:border-slate-700 dark:bg-slate-900"
                                >
                                  {ENVIRONMENTS.includes(env) ? null : <option value={env}>{env}</option>}
                                  {ENVIRONMENTS.map((e) => (
                                    <option key={e} value={e}>{e}</option>
                                  ))}
                                </select>
                                {env !== stack.suggested_environment && (
                                  <Badge tone="warning">overridden</Badge>
                                )}
                              </li>
                            );
                          })}
                        </ul>
                      </div>
                    );
                  })}
                </div>
                );
              })}
            </div>

            <div className="flex justify-end gap-2 border-t pt-4 border-slate-200 dark:border-slate-800/80">
              <Button
                type="button"
                onClick={onImport}
                disabled={importing || selectedCount === 0}
              >
                {importing ? <><Spinner /> Importing…</> : `Import ${selectedCount} workspace${selectedCount === 1 ? "" : "s"}`}
              </Button>
            </div>
          </>
        )}
      </CardBody>
    </Card>
  );
}
