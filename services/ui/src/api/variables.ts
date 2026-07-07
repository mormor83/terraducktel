import { api } from "./client";

/** Server-side variable shape. `value` is `null` for secret rows; `masked_tail`
 * (last-4 fingerprint) is populated instead so the UI can identify them. */
export type Variable = {
  id: string;
  scope: "global" | "workspace" | "run";
  workspace_id?: string | null;
  key: string;
  is_secret: boolean;
  is_hcl: boolean;
  description?: string | null;
  value?: string | null;
  masked_tail?: string | null;
};

/** Shape sent on create. */
export type VariableCreate = {
  key: string;
  value: string;
  is_secret?: boolean;
  is_hcl?: boolean;
  description?: string;
};

/** Shape sent on partial update. Omit `value` to leave the stored ciphertext
 * untouched (rotate description/flags without re-supplying a secret). */
export type VariableUpdate = {
  is_secret?: boolean;
  is_hcl?: boolean;
  description?: string;
  value?: string;
};

/** One-off variable supplied at run trigger time. Sent inline in the
 * RunCreate body; persisted encrypted on the run row. */
export type RunVariable = {
  key: string;
  value: string;
  is_secret?: boolean;
  is_hcl?: boolean;
};

// ─── globals ───────────────────────────────────────────────────────────────

export async function listGlobalVariables(): Promise<Variable[]> {
  const r = await api.get<Variable[]>("/v1/variables");
  return r.data;
}

export async function createGlobalVariable(body: VariableCreate): Promise<Variable> {
  const r = await api.post<Variable>("/v1/variables", body);
  return r.data;
}

export async function updateGlobalVariable(
  id: string,
  body: VariableUpdate,
): Promise<Variable> {
  const r = await api.patch<Variable>(`/v1/variables/${id}`, body);
  return r.data;
}

export async function deleteGlobalVariable(id: string): Promise<void> {
  await api.delete(`/v1/variables/${id}`);
}

// ─── workspace-scoped ──────────────────────────────────────────────────────

export async function listWorkspaceVariables(workspaceId: string): Promise<Variable[]> {
  const r = await api.get<Variable[]>(`/v1/workspaces/${workspaceId}/variables`);
  return r.data;
}

export async function createWorkspaceVariable(
  workspaceId: string,
  body: VariableCreate,
): Promise<Variable> {
  const r = await api.post<Variable>(`/v1/workspaces/${workspaceId}/variables`, body);
  return r.data;
}

export async function updateWorkspaceVariable(
  workspaceId: string,
  id: string,
  body: VariableUpdate,
): Promise<Variable> {
  const r = await api.patch<Variable>(
    `/v1/workspaces/${workspaceId}/variables/${id}`,
    body,
  );
  return r.data;
}

export async function deleteWorkspaceVariable(
  workspaceId: string,
  id: string,
): Promise<void> {
  await api.delete(`/v1/workspaces/${workspaceId}/variables/${id}`);
}
