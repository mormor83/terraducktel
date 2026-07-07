import { FormEvent, useEffect, useState } from "react";
import {
  createGlobalVariable,
  createWorkspaceVariable,
  deleteGlobalVariable,
  deleteWorkspaceVariable,
  listGlobalVariables,
  listWorkspaceVariables,
  updateGlobalVariable,
  updateWorkspaceVariable,
  type Variable,
  type VariableCreate,
  type VariableUpdate,
} from "../api/variables";
import { Button, Card, CardBody, CardHeader, CardTitle, ConfirmDialog, EmptyState, Input, Label, Spinner, cx } from "./ui";

/** Reusable variables CRUD panel.
 *
 * Two modes:
 *   - `mode="global"`:    targets /api/v1/variables (admin-only writes)
 *   - `mode="workspace"`: targets /api/v1/workspaces/{id}/variables (operator+)
 *
 * Same UI for both; the parent passes workspaceId only in workspace mode.
 *
 * Secret values are write-once: editing an existing secret leaves the stored
 * ciphertext alone unless the user explicitly types into the "Replace value"
 * box. This matches the server-side contract — PATCH with no `value` field
 * doesn't re-encrypt.
 */
export function VariablesPanel({
  mode,
  workspaceId,
  canWrite,
}: {
  mode: "global" | "workspace";
  workspaceId?: string;
  canWrite: boolean;
}) {
  const [vars, setVars] = useState<Variable[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState<Variable | "new" | null>(null);
  // Pending delete awaiting in-app confirmation (replaces window.confirm).
  const [pendingDelete, setPendingDelete] = useState<Variable | null>(null);
  const [actionBusy, setActionBusy] = useState(false);

  async function refresh() {
    try {
      const data = mode === "global"
        ? await listGlobalVariables()
        : await listWorkspaceVariables(workspaceId!);
      setVars(data);
      setErr(null);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? "Failed to load variables");
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, workspaceId]);

  async function save(body: VariableCreate | VariableUpdate, editingRow: Variable | "new") {
    if (editingRow === "new") {
      if (mode === "global") {
        await createGlobalVariable(body as VariableCreate);
      } else {
        await createWorkspaceVariable(workspaceId!, body as VariableCreate);
      }
    } else {
      if (mode === "global") {
        await updateGlobalVariable(editingRow.id, body as VariableUpdate);
      } else {
        await updateWorkspaceVariable(workspaceId!, editingRow.id, body as VariableUpdate);
      }
    }
    setEditing(null);
    await refresh();
  }

  async function confirmRemove() {
    const v = pendingDelete;
    if (!v) return;
    setActionBusy(true);
    try {
      if (mode === "global") {
        await deleteGlobalVariable(v.id);
      } else {
        await deleteWorkspaceVariable(workspaceId!, v.id);
      }
      await refresh();
      setPendingDelete(null);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? "Delete failed");
    } finally {
      setActionBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          {mode === "global" ? "Global variables" : "Workspace variables"}
        </CardTitle>
        {canWrite && (
          <Button size="sm" variant="primary" onClick={() => setEditing("new")}>
            Add variable
          </Button>
        )}
      </CardHeader>
      <CardBody>
        {err && (
          <div className="mb-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
            {err}
          </div>
        )}
        {vars === null ? (
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <Spinner /> Loading…
          </div>
        ) : vars.length === 0 ? (
          <EmptyState
            title="No variables yet"
            description={
              mode === "global"
                ? "Add an org-wide TF_VAR_* default. Every workspace's runs will receive it unless overridden."
                : "Per-workspace overrides go here. They beat global vars at run time."
            }
          />
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase text-slate-500">
                <th className="pb-2 pr-3 font-medium">Key</th>
                <th className="pb-2 pr-3 font-medium">Value</th>
                <th className="pb-2 pr-3 font-medium">Type</th>
                <th className="pb-2 pr-3 font-medium">Notes</th>
                {canWrite && <th className="pb-2 font-medium">Actions</th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
              {vars.map((v) => (
                <tr key={v.id}>
                  <td className="py-2 pr-3 font-mono text-xs">{v.key}</td>
                  <td className="py-2 pr-3 font-mono text-xs">
                    {v.is_secret ? (
                      <span className="text-slate-500">
                        <SecretBadge /> {v.masked_tail ?? "•••"}
                      </span>
                    ) : (
                      <span className="break-all">{v.value ?? ""}</span>
                    )}
                  </td>
                  <td className="py-2 pr-3 text-xs">
                    {v.is_hcl ? <Pill tone="amber">HCL</Pill> : <Pill tone="slate">string</Pill>}
                  </td>
                  <td className="py-2 pr-3 text-xs text-slate-500">{v.description}</td>
                  {canWrite && (
                    <td className="py-2 text-xs">
                      <button
                        type="button"
                        className="text-sky-600 hover:underline dark:text-sky-400"
                        onClick={() => setEditing(v)}
                      >
                        edit
                      </button>
                      <button
                        type="button"
                        className="ml-3 text-red-600 hover:underline dark:text-red-400"
                        onClick={() => setPendingDelete(v)}
                      >
                        delete
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardBody>
      {editing && (
        <VariableEditor
          initial={editing === "new" ? null : editing}
          onCancel={() => setEditing(null)}
          onSave={(body) => save(body, editing)}
        />
      )}
      <ConfirmDialog
        open={pendingDelete !== null}
        tone="danger"
        title="Delete variable"
        message={<>Delete variable <strong>"{pendingDelete?.key}"</strong>?</>}
        confirmLabel="Delete"
        busy={actionBusy}
        onConfirm={confirmRemove}
        onCancel={() => setPendingDelete(null)}
      />
    </Card>
  );
}

function Pill({ children, tone }: { children: React.ReactNode; tone: "amber" | "slate" }) {
  return (
    <span
      className={cx(
        "inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium ring-1 ring-inset",
        tone === "amber"
          ? "bg-amber-100 text-amber-800 ring-amber-300/70 dark:bg-amber-900/30 dark:text-amber-200 dark:ring-amber-700/50"
          : "bg-slate-100 text-slate-700 ring-slate-300/80 dark:bg-slate-800/80 dark:text-slate-300 dark:ring-slate-700/50",
      )}
    >
      {children}
    </span>
  );
}

function SecretBadge() {
  return (
    <span
      className="mr-1 inline-flex items-center rounded bg-purple-100 px-1.5 py-0.5 text-[10px] font-medium text-purple-800 ring-1 ring-inset ring-purple-300/70 dark:bg-purple-900/30 dark:text-purple-200 dark:ring-purple-700/50"
      title="Stored encrypted — value never returned via API after first save"
    >
      🔒 secret
    </span>
  );
}

// ─── editor modal ──────────────────────────────────────────────────────────

function VariableEditor({
  initial,
  onCancel,
  onSave,
}: {
  initial: Variable | null;
  onCancel: () => void;
  onSave: (body: VariableCreate | VariableUpdate) => Promise<void>;
}) {
  const isNew = initial === null;
  const [key, setKey] = useState(initial?.key ?? "");
  const [value, setValue] = useState<string>(
    initial && !initial.is_secret ? initial.value ?? "" : "",
  );
  const [replaceSecret, setReplaceSecret] = useState(false);
  const [isSecret, setIsSecret] = useState(initial?.is_secret ?? false);
  const [isHcl, setIsHcl] = useState(initial?.is_hcl ?? false);
  const [description, setDescription] = useState(initial?.description ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      if (isNew) {
        await onSave({
          key: key.trim(),
          value,
          is_secret: isSecret,
          is_hcl: isHcl,
          description: description.trim() || undefined,
        });
      } else {
        const body: VariableUpdate = {
          is_secret: isSecret,
          is_hcl: isHcl,
          description: description.trim(),
        };
        // For existing rows, only re-encrypt when the operator opted in (secret
        // case) or whenever the value field is visible (non-secret case).
        if (!initial!.is_secret) {
          body.value = value;
        } else if (replaceSecret) {
          body.value = value;
        }
        await onSave(body);
      }
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? "Save failed");
    } finally {
      setBusy(false);
    }
  }

  // Lock key field when editing an existing row — server rejects key changes
  // (immutable post-create per the service-layer contract).
  const keyDisabled = !isNew;

  return (
    <div
      role="dialog"
      aria-label={isNew ? "Add variable" : "Edit variable"}
      className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4 backdrop-blur-sm"
    >
      <div className="w-full max-w-lg rounded-lg border border-slate-200 bg-white shadow-xl dark:border-slate-700/80 dark:bg-slate-900">
        <div className="border-b border-slate-200 px-4 py-3 dark:border-slate-800">
          <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            {isNew ? "Add variable" : `Edit ${initial!.key}`}
          </h3>
        </div>
        <form onSubmit={submit} className="space-y-3 px-4 py-3">
          <div>
            <Label>Key</Label>
            <Input
              type="text"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              disabled={keyDisabled}
              placeholder="my_variable"
              required
            />
            <p className="mt-0.5 text-[11px] text-slate-500">
              Terraform identifier rules: letters/digits/underscores, no leading digit.
            </p>
          </div>

          {(!isSecret || isNew || replaceSecret) ? (
            <div>
              <Label>{isHcl ? "Value (HCL)" : "Value"}</Label>
              <textarea
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder={isHcl ? '["a", "b"]' : "string value"}
                rows={isHcl ? 4 : 2}
                className="w-full rounded-md border border-slate-300 bg-white px-2.5 py-1.5 font-mono text-xs focus:border-sky-500 focus:outline-none dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
              />
            </div>
          ) : (
            <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs dark:border-slate-700 dark:bg-slate-900/60">
              <div className="font-mono text-slate-600 dark:text-slate-400">
                value: <SecretBadge /> {initial!.masked_tail ?? "•••"}
              </div>
              <button
                type="button"
                className="mt-1 text-[11px] text-sky-600 hover:underline dark:text-sky-400"
                onClick={() => { setReplaceSecret(true); setValue(""); }}
              >
                Replace value
              </button>
            </div>
          )}

          <div className="flex flex-wrap gap-4 text-xs">
            <label className="flex cursor-pointer items-center gap-2">
              <input
                type="checkbox"
                checked={isSecret}
                onChange={(e) => setIsSecret(e.target.checked)}
                className="accent-sky-600"
              />
              <span>Secret (mask after save, write-once)</span>
            </label>
            <label className="flex cursor-pointer items-center gap-2">
              <input
                type="checkbox"
                checked={isHcl}
                onChange={(e) => setIsHcl(e.target.checked)}
                className="accent-sky-600"
              />
              <span>HCL expression (list/map/etc.)</span>
            </label>
          </div>

          <div>
            <Label>Description (optional)</Label>
            <Input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What is this used for?"
            />
          </div>

          {err && (
            <div className="rounded-md border border-red-200 bg-red-50 px-2.5 py-1.5 text-xs text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
              {err}
            </div>
          )}

          <div className="flex items-center justify-end gap-2 border-t border-slate-200 pt-3 dark:border-slate-800">
            <Button type="button" size="sm" variant="ghost" onClick={onCancel} disabled={busy}>
              Cancel
            </Button>
            <Button type="submit" size="sm" variant="primary" disabled={busy}>
              {busy ? "Saving…" : isNew ? "Create" : "Save"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
