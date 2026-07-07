import { FormEvent, useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import {
  type ApiKey,
  type ApiKeyCreated,
  type Capability,
  createApiKey,
  listApiKeys,
  regenerateApiKey,
  revokeApiKey,
} from "../../api/apiKeys";
import { useBusinessUnits, useBusinessUnitSelection } from "../../hooks/useBusinessUnit";
import CopyButton from "../CopyButton";
import {
  Badge,
  Button,
  Card,
  CardBody,
  ConfirmDialog,
  Input,
  Label,
  Select,
  Spinner,
  cx,
} from "../ui";

type WsLite = { id: string; name: string; environment?: string; region?: string };

const CAP_HELP: Record<Capability, string> = {
  read: "Read-only — list/inspect runs, plans, and workspaces.",
  plan: "Trigger plan-only runs (and cancel them). Cannot approve or apply.",
  apply: "Trigger plan/apply/destroy runs and approve/reject. Full run automation.",
  admin:
    "Full admin within this BU: everything apply can do, PLUS discover repos, " +
    "create/update/delete workspaces, manage AWS accounts, clusters, policies, " +
    "drift and integrations. Cannot manage users, Business Units, or other API " +
    "keys. Powerful — treat the token like an admin password.",
};

const CAP_TONE: Record<Capability, "neutral" | "info" | "warning" | "danger"> = {
  read: "neutral",
  plan: "info",
  apply: "warning",
  admin: "danger",
};

function ScopeBadge() {
  const [slug] = useBusinessUnitSelection();
  const { bus } = useBusinessUnits();
  const label =
    slug === null || slug === ""
      ? "(no BU selected)"
      : bus.find((b) => b.slug === slug)?.name ?? slug;
  return (
    <div className="mb-3 inline-flex items-center gap-2 rounded-md border border-brand-border bg-brand-surface2 px-2.5 py-1 text-xs text-brand-textSoft dark:border-brand-700 dark:bg-brand-800/40 dark:text-brand-100/80">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
        <path d="M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V7M3 7l9-4 9 4M3 7l9 4 9-4" />
      </svg>
      Keys are scoped to BU: <strong className="text-brand-text dark:text-brand-100">{label}</strong>
    </div>
  );
}

export default function ApiKeysSection() {
  const [buSlug] = useBusinessUnitSelection();
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [workspaces, setWorkspaces] = useState<WsLite[]>([]);
  const [err, setErr] = useState<string | null>(null);

  // Create-form state.
  const [name, setName] = useState("");
  const [capability, setCapability] = useState<Capability>("read");
  const [selectedWs, setSelectedWs] = useState<Set<string>>(new Set());
  const [expiresAt, setExpiresAt] = useState("");
  const [creating, setCreating] = useState(false);

  // The freshly-minted plaintext token — shown once until dismissed.
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);

  // Pending destructive action awaiting in-app confirmation (no native popups).
  const [confirmAction, setConfirmAction] = useState<
    { kind: "revoke" | "regenerate"; key: ApiKey } | null
  >(null);
  const [actionBusy, setActionBusy] = useState(false);

  async function load() {
    setErr(null);
    try {
      const [k, w] = await Promise.all([
        listApiKeys(),
        api.get<WsLite[]>("/v1/workspaces").catch(() => ({ data: [] as WsLite[] })),
      ]);
      setKeys(k);
      setWorkspaces(w.data);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? "Failed to load API keys");
      setKeys([]);
    }
  }

  // Reload whenever the selected BU changes — keys are BU-scoped.
  useEffect(() => {
    setKeys(null);
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buSlug]);

  function toggleWs(id: string) {
    setSelectedWs((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setCreating(true);
    setErr(null);
    try {
      const key = await createApiKey({
        name: name.trim(),
        capability,
        workspace_ids: selectedWs.size ? Array.from(selectedWs) : null,
        expires_at: expiresAt ? new Date(expiresAt).toISOString() : null,
      });
      setCreated(key);
      // Reset the form and refresh the list.
      setName("");
      setCapability("read");
      setSelectedWs(new Set());
      setExpiresAt("");
      await load();
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? "Failed to create API key");
    } finally {
      setCreating(false);
    }
  }

  async function runConfirmedAction() {
    if (!confirmAction) return;
    const { kind, key } = confirmAction;
    setActionBusy(true);
    setErr(null);
    try {
      if (kind === "revoke") {
        await revokeApiKey(key.id);
        await load();
      } else {
        const rotated = await regenerateApiKey(key.id);
        setCreated(rotated);
        await load();
      }
      setConfirmAction(null);
    } catch (e: any) {
      setErr(
        e?.response?.data?.detail ??
          (kind === "revoke" ? "Revoke failed" : "Regenerate failed"),
      );
    } finally {
      setActionBusy(false);
    }
  }

  const wsName = useMemo(() => {
    const m = new Map(workspaces.map((w) => [w.id, w.name]));
    return (id: string) => m.get(id) ?? id.slice(0, 8);
  }, [workspaces]);

  return (
    <div className="space-y-6">
      <ScopeBadge />

      <div>
        <h3 className="text-sm font-semibold text-brand-text dark:text-brand-100">API keys</h3>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          Long-lived credentials for automation. Use as a bearer token:{" "}
          <code className="font-mono">Authorization: Bearer tdt_…</code>. A key acts only within
          this Business Unit, at its capability tier, and (optionally) only on the workspaces you
          select.
        </p>
      </div>

      {err && (
        <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
          {err}
        </p>
      )}

      {/* One-time plaintext token reveal. */}
      {created && (
        <Card className="border-emerald-300 dark:border-emerald-900/50">
          <CardBody className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold text-emerald-700 dark:text-emerald-300">
                Key "{created.name}" — copy the token now
              </span>
              <button
                type="button"
                onClick={() => setCreated(null)}
                className="rounded-md px-2 py-1 text-xs text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
              >
                Dismiss
              </button>
            </div>
            <p className="text-xs text-amber-700 dark:text-amber-300">
              This is the only time the token is shown. Store it securely — it cannot be retrieved later.
            </p>
            <div className="flex items-center gap-2">
              <code className="flex-1 overflow-x-auto rounded-md border border-slate-200 bg-slate-50 px-3 py-2 font-mono text-xs text-slate-800 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200">
                {created.token}
              </code>
              <CopyButton getText={() => created.token} title="Copy token" />
            </div>
          </CardBody>
        </Card>
      )}

      {/* Create form. */}
      <Card>
        <CardBody>
          <form onSubmit={submit} className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label htmlFor="apikey-name">Name</Label>
                <Input
                  id="apikey-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="ci-deploy-bot"
                  required
                />
              </div>
              <div>
                <Label htmlFor="apikey-cap">Capability</Label>
                <Select
                  id="apikey-cap"
                  value={capability}
                  onChange={(e) => setCapability(e.target.value as Capability)}
                >
                  <option value="read">read — read-only</option>
                  <option value="plan">plan — trigger plans</option>
                  <option value="apply">apply — plan + approve + apply</option>
                  <option value="admin">admin — full control (incl. workspaces)</option>
                </Select>
                <p
                  className={cx(
                    "mt-1 text-[11px]",
                    capability === "admin"
                      ? "font-medium text-red-600 dark:text-red-400"
                      : "text-slate-500",
                  )}
                >
                  {CAP_HELP[capability]}
                </p>
              </div>
            </div>

            <div>
              <Label>Workspace scope</Label>
              <p className="mb-1.5 text-[11px] text-slate-500">
                Leave all unchecked to allow every workspace in this BU. Check specific workspaces to
                restrict the key.
              </p>
              <div className="max-h-44 overflow-auto rounded-md border border-slate-200 p-2 dark:border-slate-700">
                {workspaces.length === 0 ? (
                  <p className="px-1 py-2 text-xs italic text-slate-500">No workspaces in this BU.</p>
                ) : (
                  workspaces.map((w) => (
                    <label
                      key={w.id}
                      className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-xs hover:bg-slate-50 dark:hover:bg-slate-800/60"
                    >
                      <input
                        type="checkbox"
                        checked={selectedWs.has(w.id)}
                        onChange={() => toggleWs(w.id)}
                      />
                      <span className="font-medium text-slate-700 dark:text-slate-200">{w.name}</span>
                      {w.environment && (
                        <span className="text-slate-400">· {w.environment}</span>
                      )}
                    </label>
                  ))
                )}
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label htmlFor="apikey-exp">Expires (optional)</Label>
                <Input
                  id="apikey-exp"
                  type="date"
                  value={expiresAt}
                  onChange={(e) => setExpiresAt(e.target.value)}
                />
                <p className="mt-1 text-[11px] text-slate-500">Leave empty for a non-expiring key.</p>
              </div>
            </div>

            <div>
              <Button type="submit" disabled={creating || !name.trim()}>
                {creating ? "Creating…" : "Create API key"}
              </Button>
            </div>
          </form>
        </CardBody>
      </Card>

      {/* Existing keys. */}
      <div>
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
          Existing keys
        </h4>
        {keys === null ? (
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <Spinner /> Loading…
          </div>
        ) : keys.length === 0 ? (
          <p className="text-sm italic text-slate-500">No API keys in this Business Unit yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wider text-slate-500 dark:border-slate-700">
                  <th className="py-2 pr-3">Name</th>
                  <th className="py-2 pr-3">Capability</th>
                  <th className="py-2 pr-3">Scope</th>
                  <th className="py-2 pr-3">Prefix</th>
                  <th className="py-2 pr-3">Expires</th>
                  <th className="py-2 pr-3">Last used</th>
                  <th className="py-2 pr-3"></th>
                </tr>
              </thead>
              <tbody>
                {keys.map((k) => {
                  const revoked = !!k.revoked_at;
                  // Expired keys can't be rotated — regenerate would mint a
                  // born-expired secret — so only offer it on live keys.
                  const expired =
                    !!k.expires_at && new Date(k.expires_at).getTime() <= Date.now();
                  return (
                    <tr
                      key={k.id}
                      className={cx(
                        "border-b border-slate-100 dark:border-slate-800/60",
                        revoked && "opacity-50",
                      )}
                    >
                      <td className="py-2 pr-3 font-medium text-slate-700 dark:text-slate-200">
                        {k.name}
                        {revoked && <span className="ml-2 text-[11px] text-red-500">revoked</span>}
                      </td>
                      <td className="py-2 pr-3">
                        <Badge tone={CAP_TONE[k.capability]}>{k.capability}</Badge>
                      </td>
                      <td className="py-2 pr-3 text-xs text-slate-600 dark:text-slate-300">
                        {k.workspace_ids && k.workspace_ids.length
                          ? k.workspace_ids.map(wsName).join(", ")
                          : "All workspaces"}
                      </td>
                      <td className="py-2 pr-3 font-mono text-xs text-slate-500">{k.token_prefix}…</td>
                      <td className="py-2 pr-3 text-xs text-slate-500">
                        {k.expires_at ? new Date(k.expires_at).toLocaleDateString() : "Never"}
                      </td>
                      <td className="py-2 pr-3 text-xs text-slate-500">
                        {k.last_used_at ? new Date(k.last_used_at).toLocaleString() : "—"}
                      </td>
                      <td className="py-2 pr-3 text-right">
                        {!revoked && (
                          <div className="flex justify-end gap-1">
                            {!expired && (
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => setConfirmAction({ kind: "regenerate", key: k })}
                              >
                                Regenerate
                              </Button>
                            )}
                            <Button
                              size="sm"
                              variant="ghost"
                              className="text-red-500 hover:text-red-400"
                              onClick={() => setConfirmAction({ kind: "revoke", key: k })}
                            >
                              Revoke
                            </Button>
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <ConfirmDialog
        open={confirmAction !== null}
        tone={confirmAction?.kind === "revoke" ? "danger" : "warning"}
        title={confirmAction?.kind === "revoke" ? "Revoke API key" : "Regenerate API key"}
        message={
          confirmAction?.kind === "revoke" ? (
            <>
              Revoke API key <strong>"{confirmAction?.key.name}"</strong>? Automation using it will
              stop working immediately. This cannot be undone.
            </>
          ) : (
            <>
              Regenerate API key <strong>"{confirmAction?.key.name}"</strong>? A new token will be
              issued and shown once; the current token stops working immediately. The key keeps its
              capability, scope and expiry.
            </>
          )
        }
        confirmLabel={confirmAction?.kind === "revoke" ? "Revoke" : "Regenerate"}
        busy={actionBusy}
        onConfirm={runConfirmedAction}
        onCancel={() => setConfirmAction(null)}
      />
    </div>
  );
}
