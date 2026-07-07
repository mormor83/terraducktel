import { FormEvent, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import {
  listGlobalVariables,
  listWorkspaceVariables,
  type RunVariable,
  type Variable,
} from "../api/variables";
import { Button, Input, Label, Spinner, cx } from "./ui";

type BranchesResponse = {
  source: "github" | "none";
  default_branch: string;
  branches: string[];
  message?: string;
};

type LocalRunVar = RunVariable & { tempId: string };

/** Unified Run modal.
 *
 * One screen, one submit: pick a branch, optionally add per-run variables,
 * then start. Any operator+ user can run (4-eyes was removed).
 *
 * Two terminal actions:
 *   - "Run":  persists the chosen branch onto the workspace AND spawns a run.
 *   - "Update branch only": persists the branch without spawning a run.
 *     The per-run var rows are discarded — they only make sense with a run.
 */
export function RunModal({
  workspaceId,
  workspaceName,
  currentBranch,
  command,
  onClose,
  onDone,
}: {
  workspaceId: string;
  workspaceName: string;
  currentBranch: string;
  command: "apply" | "destroy";
  onClose: () => void;
  onDone: () => void;
}) {
  // ─── branches + selection ──────────────────────────────────────────────
  const [branchData, setBranchData] = useState<BranchesResponse | null>(null);
  const [branch, setBranch] = useState(currentBranch);
  const [branchFilter, setBranchFilter] = useState("");

  // ─── effective vars preview + run-scope additions ──────────────────────
  const [globals, setGlobals] = useState<Variable[]>([]);
  const [workspaceVars, setWorkspaceVars] = useState<Variable[]>([]);
  const [runVars, setRunVars] = useState<LocalRunVar[]>([]);

  const [busy, setBusy] = useState<"none" | "run" | "branch">("none");
  const [err, setErr] = useState<string | null>(null);
  // Auto-approve opt-ins. Only meaningful for apply/destroy commands (the
  // plan-only command never reaches an apply phase to approve). The
  // checkbox row is hidden for plan-only runs to keep the form quiet.
  const [autoApprove, setAutoApprove] = useState(false);
  const [autoApproveSkipApply, setAutoApproveSkipApply] = useState(true);

  const cardRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  // Initial loads — run in parallel; tolerate individual failures so the
  // operator can still fall back to typing a branch by hand.
  useEffect(() => {
    let alive = true;
    Promise.allSettled([
      api.get<BranchesResponse>(`/v1/workspaces/${workspaceId}/branches`),
      listGlobalVariables(),
      listWorkspaceVariables(workspaceId),
    ]).then(([b, g, w]) => {
      if (!alive) return;
      if (b.status === "fulfilled") setBranchData(b.value.data);
      else setBranchData({ source: "none", default_branch: currentBranch, branches: [], message: "Failed to load branches." });
      if (g.status === "fulfilled") setGlobals(g.value);
      if (w.status === "fulfilled") setWorkspaceVars(w.value);
    });
    return () => { alive = false; };
  }, [workspaceId, currentBranch]);

  // ESC / click-outside close.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    function onClick(e: MouseEvent) {
      if (cardRef.current && !cardRef.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [onClose]);

  // ─── effective var merge for preview only ─────────────────────────────
  // Mirrors the server's get_merged_for_run precedence: global ← workspace ← run.
  type Effective = { key: string; source: "global" | "workspace" | "run"; v: Variable | LocalRunVar };
  const effective: Effective[] = (() => {
    const out = new Map<string, Effective>();
    for (const g of globals) out.set(g.key, { key: g.key, source: "global", v: g });
    for (const w of workspaceVars) out.set(w.key, { key: w.key, source: "workspace", v: w });
    for (const r of runVars) out.set(r.key, { key: r.key, source: "run", v: r });
    return [...out.values()].sort((a, b) => a.key.localeCompare(b.key));
  })();

  function addRunVar() {
    setRunVars((prev) => [...prev, {
      tempId: `${Date.now()}-${Math.random()}`,
      key: "", value: "", is_secret: false, is_hcl: false,
    }]);
  }
  function setRunVar(tempId: string, patch: Partial<LocalRunVar>) {
    setRunVars((prev) => prev.map((v) => v.tempId === tempId ? { ...v, ...patch } : v));
  }
  function removeRunVar(tempId: string) {
    setRunVars((prev) => prev.filter((v) => v.tempId !== tempId));
  }

  // ─── submit handlers ──────────────────────────────────────────────────

  async function submitRun(e: FormEvent) {
    e.preventDefault();
    if (!branch.trim()) {
      setErr("Branch is required");
      return;
    }
    // Strip empty rows; the API will 422 on bad keys, so let it.
    const variables: RunVariable[] = runVars
      .filter((v) => v.key.trim().length > 0)
      .map(({ tempId, ...rest }) => rest);
    setBusy("run");
    setErr(null);
    try {
      const res = await api.post(`/v1/workspaces/${workspaceId}/runs`, {
        command,
        branch: branch.trim(),
        variables: variables.length ? variables : undefined,
        auto_approve_if_no_changes: autoApprove,
        auto_approve_skip_apply: autoApprove && autoApproveSkipApply,
      });
      onDone();
      onClose();
      // Jump straight to the new run so the operator can watch it progress and
      // approve/reject inline when it reaches awaiting_approval — no need to
      // bounce through the Runs list.
      const newRunId = res.data?.id;
      if (newRunId) navigate(`/runs/${newRunId}`);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? "Failed to start run");
    } finally {
      setBusy("none");
    }
  }

  async function submitBranchOnly() {
    if (!branch.trim()) {
      setErr("Branch is required");
      return;
    }
    setBusy("branch");
    setErr(null);
    try {
      await api.put(`/v1/workspaces/${workspaceId}`, { repo_ref: branch.trim() });
      onDone();
      onClose();
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? "Failed to update branch");
    } finally {
      setBusy("none");
    }
  }

  // ─── render ────────────────────────────────────────────────────────────

  const branches = branchData?.branches ?? [];
  const filteredBranches = branchFilter
    ? branches.filter((b) => b.toLowerCase().includes(branchFilter.toLowerCase()))
    : branches;
  const branchSourceUnavailable = branchData !== null && branchData.source === "none";

  return (
    <div
      role="dialog"
      aria-label={command === "destroy" ? "Destroy workspace" : "Run workspace"}
      className="fixed inset-0 z-50 grid place-items-start justify-center bg-black/40 p-4 pt-16 backdrop-blur-sm"
    >
      <div
        ref={cardRef}
        className="w-full max-w-2xl rounded-lg border border-slate-200 bg-white shadow-xl dark:border-slate-700/80 dark:bg-slate-900"
      >
        <div className="border-b border-slate-200 px-5 py-3 dark:border-slate-800">
          <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            {command === "destroy" ? "Destroy" : "Run"} <span className="font-mono">{workspaceName}</span>
          </h3>
          <p className="mt-0.5 text-xs text-slate-500">
            Choose the branch, set variables for this run, then start. Selecting a new branch
            updates the workspace's tracked branch — use "Update branch only" to change it without running.
          </p>
        </div>

        <form onSubmit={submitRun} className="space-y-5 px-5 py-4">
          {/* ─── Branch ─── */}
          <section>
            <Label>Branch</Label>
            {branchData === null ? (
              <div className="mt-1 flex items-center gap-2 text-xs text-slate-500"><Spinner /> Loading branches…</div>
            ) : !branchSourceUnavailable ? (
              <>
                <input
                  type="text"
                  value={branchFilter}
                  onChange={(e) => setBranchFilter(e.target.value)}
                  placeholder="Filter branches…"
                  className="mt-1 w-full rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm focus:border-sky-500 focus:outline-none dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
                />
                <div className="mt-1.5 max-h-40 overflow-auto rounded-md border border-slate-200 dark:border-slate-700/70">
                  {filteredBranches.length === 0 ? (
                    <p className="px-3 py-3 text-xs text-slate-500">No branches match.</p>
                  ) : (
                    <ul className="divide-y divide-slate-100 dark:divide-slate-800/60">
                      {filteredBranches.map((b) => (
                        <li key={b}>
                          <button
                            type="button"
                            onClick={() => setBranch(b)}
                            className={cx(
                              "flex w-full items-center justify-between px-3 py-1.5 text-left text-sm transition-colors",
                              branch === b
                                ? "bg-sky-50 font-medium text-sky-700 dark:bg-sky-900/30 dark:text-sky-200"
                                : "hover:bg-slate-50 dark:hover:bg-slate-800/40",
                            )}
                          >
                            <span className="font-mono">{b}</span>
                            {b === branchData!.default_branch && (
                              <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-400">
                                default
                              </span>
                            )}
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <span className="text-[11px] uppercase tracking-wide text-slate-500">Selected branch</span>
                  {(() => {
                    // Color non-default branches (amber) so running anything other
                    // than the repo's default branch is visually obvious; the
                    // default branch stays neutral.
                    const isDefault = branch === branchData?.default_branch;
                    return (
                      <span
                        className={cx(
                          "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset",
                          isDefault
                            ? "bg-slate-100 text-slate-700 ring-slate-300/70 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-700/60"
                            : "bg-amber-100 text-amber-800 ring-amber-300/70 dark:bg-amber-900/40 dark:text-amber-200 dark:ring-amber-700/60",
                        )}
                      >
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
                          <path d="M6 3a3 3 0 0 1 1 5.83V15a3 3 0 0 0 3 3h1V14a3 3 0 1 1 2 0v4h1a5 5 0 0 0 5-5V8.83A3 3 0 1 0 17 3a3 3 0 0 0 0 5.83V13a3 3 0 0 1-3 3h-1v-2a3 3 0 0 0-2-2.83V8.83A3 3 0 0 0 6 3Z" />
                        </svg>
                        <span className="font-mono">{branch}</span>
                        {isDefault && <span className="font-normal opacity-70">· default</span>}
                      </span>
                    );
                  })()}
                </div>
              </>
            ) : (
              <>
                <p className="mt-1 mb-1 text-xs text-amber-600 dark:text-amber-400">
                  {branchData?.message ?? "Branches couldn't be loaded — type one manually."}
                </p>
                <Input
                  type="text"
                  value={branch}
                  onChange={(e) => setBranch(e.target.value)}
                  placeholder="main"
                />
              </>
            )}
          </section>

          {/* ─── Variables: effective preview ─── */}
          <section>
            <Label>Effective variables for this run</Label>
            <p className="mb-1 text-[11px] text-slate-500">
              Merged precedence: global ← workspace ← run-scope (last wins). Secrets are masked.
            </p>
            {effective.length === 0 ? (
              <p className="rounded-md border border-dashed border-slate-300 px-3 py-2 text-xs text-slate-500 dark:border-slate-700">
                No variables. Add one below if your terraform code needs any.
              </p>
            ) : (
              <ul className="max-h-32 overflow-auto rounded-md border border-slate-200 text-xs dark:border-slate-700">
                {effective.map((e) => (
                  <li key={e.key} className="flex items-center justify-between gap-3 border-b border-slate-100 px-3 py-1 last:border-0 dark:border-slate-800/60">
                    <span className="font-mono">{e.key}</span>
                    <span className="flex-1 truncate font-mono text-slate-600 dark:text-slate-400">
                      {renderEffectiveValue(e.v)}
                    </span>
                    <SourcePill source={e.source} />
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* ─── Run-scope additions ─── */}
          <section>
            <div className="flex items-center justify-between">
              <Label>Add for this run only</Label>
              <button
                type="button"
                onClick={addRunVar}
                className="text-xs text-sky-600 hover:underline dark:text-sky-400"
              >
                + add variable
              </button>
            </div>
            {runVars.length === 0 ? (
              <p className="text-[11px] text-slate-500">
                Per-run additions/overrides go here. Not persisted on the workspace.
              </p>
            ) : (
              <div className="space-y-1.5">
                {runVars.map((v) => (
                  <div
                    key={v.tempId}
                    className="grid grid-cols-[1fr_2fr_auto_auto_auto] items-center gap-2 rounded-md border border-slate-200 px-2 py-1.5 dark:border-slate-700"
                  >
                    <input
                      type="text"
                      placeholder="key"
                      value={v.key}
                      onChange={(e) => setRunVar(v.tempId, { key: e.target.value })}
                      className="rounded border border-slate-300 px-1.5 py-1 font-mono text-xs dark:border-slate-700 dark:bg-slate-950"
                    />
                    <input
                      type={v.is_secret ? "password" : "text"}
                      placeholder={v.is_hcl ? '["a", "b"]' : "value"}
                      value={v.value}
                      onChange={(e) => setRunVar(v.tempId, { value: e.target.value })}
                      className="rounded border border-slate-300 px-1.5 py-1 font-mono text-xs dark:border-slate-700 dark:bg-slate-950"
                    />
                    <label className="flex items-center gap-1 text-[11px]" title="HCL expression">
                      <input
                        type="checkbox"
                        checked={v.is_hcl}
                        onChange={(e) => setRunVar(v.tempId, { is_hcl: e.target.checked })}
                        className="accent-sky-600"
                      />
                      hcl
                    </label>
                    <label className="flex items-center gap-1 text-[11px]" title="Secret (mask)">
                      <input
                        type="checkbox"
                        checked={v.is_secret}
                        onChange={(e) => setRunVar(v.tempId, { is_secret: e.target.checked })}
                        className="accent-purple-600"
                      />
                      secret
                    </label>
                    <button
                      type="button"
                      onClick={() => removeRunVar(v.tempId)}
                      className="text-xs text-red-600 hover:underline dark:text-red-400"
                    >
                      remove
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>

          {(command === "apply" || command === "destroy") && (
            <section className="rounded-md border border-slate-200 bg-slate-50/60 p-3 text-xs dark:border-slate-800 dark:bg-slate-900/40">
              <label className="flex cursor-pointer items-start gap-2">
                <input
                  type="checkbox"
                  checked={autoApprove}
                  onChange={(e) => setAutoApprove(e.target.checked)}
                  className="mt-0.5 h-3.5 w-3.5"
                />
                <span>
                  <span className="font-medium text-slate-800 dark:text-slate-200">
                    Auto-approve if plan shows no changes
                  </span>
                  <span className="ml-2 text-slate-500">
                    Plan must be 0/0/0 and all gates green. Audit log captures the
                    auto-approval; if a Slack channel is configured, a message is posted.
                  </span>
                </span>
              </label>
              {autoApprove && (
                <label className="mt-2 ml-6 flex cursor-pointer items-start gap-2">
                  <input
                    type="checkbox"
                    checked={autoApproveSkipApply}
                    onChange={(e) => setAutoApproveSkipApply(e.target.checked)}
                    className="mt-0.5 h-3.5 w-3.5"
                  />
                  <span>
                    <span className="font-medium text-slate-800 dark:text-slate-200">
                      Skip apply phase
                    </span>
                    <span className="ml-2 text-slate-500">
                      Faster — when off, a no-op apply still runs so the timeline matches a
                      normal apply.
                    </span>
                  </span>
                </label>
              )}
            </section>
          )}

          {err && (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
              {err}
            </div>
          )}

          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-200 pt-3 dark:border-slate-800">
            <Button type="button" size="sm" variant="ghost" onClick={onClose} disabled={busy !== "none"}>
              Cancel
            </Button>
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={submitBranchOnly}
              disabled={busy !== "none"}
              title="Persist the chosen branch on the workspace without spawning a run."
            >
              {busy === "branch" ? "Saving…" : "Update branch only"}
            </Button>
            <Button
              type="submit"
              size="sm"
              variant={command === "destroy" ? "danger" : "warning"}
              disabled={busy !== "none"}
            >
              {busy === "run" ? "Starting…" : command === "destroy" ? "Destroy" : "Run"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}

function renderEffectiveValue(v: Variable | LocalRunVar): string {
  if ("masked_tail" in v && v.is_secret) return `🔒 ${v.masked_tail ?? "•••"}`;
  if ("is_secret" in v && v.is_secret) return "🔒 •••";
  return (v as Variable).value ?? (v as LocalRunVar).value ?? "";
}

function SourcePill({ source }: { source: "global" | "workspace" | "run" }) {
  const styles = {
    global: "bg-slate-100 text-slate-700 ring-slate-300/80 dark:bg-slate-800/80 dark:text-slate-300 dark:ring-slate-700/50",
    workspace: "bg-sky-100 text-sky-800 ring-sky-300/70 dark:bg-sky-900/30 dark:text-sky-200 dark:ring-sky-700/50",
    run: "bg-amber-100 text-amber-800 ring-amber-300/70 dark:bg-amber-900/30 dark:text-amber-200 dark:ring-amber-700/50",
  }[source];
  return (
    <span className={cx("inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium ring-1 ring-inset", styles)}>
      {source}
    </span>
  );
}
