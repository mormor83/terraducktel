import { api } from "./client";

export type Capability = "read" | "plan" | "apply" | "admin";

export interface ApiKey {
  id: string;
  name: string;
  token_prefix: string;
  capability: Capability;
  workspace_ids?: string[] | null;
  business_unit_id: string;
  user_id: string;
  created_by?: string | null;
  created_at: string;
  last_used_at?: string | null;
  expires_at?: string | null;
  revoked_at?: string | null;
}

export interface ApiKeyCreate {
  name: string;
  capability: Capability;
  workspace_ids?: string[] | null;
  expires_at?: string | null;
}

// Returned exactly once, on creation — carries the plaintext token.
export interface ApiKeyCreated extends ApiKey {
  token: string;
}

export async function listApiKeys(): Promise<ApiKey[]> {
  const res = await api.get<ApiKey[]>("/v1/api-keys");
  return res.data;
}

export async function createApiKey(body: ApiKeyCreate): Promise<ApiKeyCreated> {
  const res = await api.post<ApiKeyCreated>("/v1/api-keys", body);
  return res.data;
}

export async function revokeApiKey(id: string): Promise<void> {
  await api.delete(`/v1/api-keys/${id}`);
}

// Rotate a key's secret in place — same name/capability/scope/expiry, fresh
// token (returned once, like creation). The old token stops working at once.
export async function regenerateApiKey(id: string): Promise<ApiKeyCreated> {
  const res = await api.post<ApiKeyCreated>(`/v1/api-keys/${id}/regenerate`, {});
  return res.data;
}
