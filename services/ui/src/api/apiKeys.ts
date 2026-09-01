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
  // Set when a rotation superseded this key; `expires_at` is then the end of
  // its overlap window rather than an expiry someone chose.
  rotated_at?: string | null;
  superseded_by_id?: string | null;
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

// Replace a key's secret in place — same name/capability/scope/expiry, fresh
// token (returned once, like creation). The old token stops working at once.
// Use this only when a single consumer holds the key.
export async function regenerateApiKey(id: string): Promise<ApiKeyCreated> {
  const res = await api.post<ApiKeyCreated>(`/v1/api-keys/${id}/regenerate`, {});
  return res.data;
}

// Returned once by a rotation: the successor key, plus the deadline for
// retiring the one it replaced.
export interface ApiKeyRotated extends ApiKeyCreated {
  predecessor_id: string;
  predecessor_expires_at?: string | null;
}

// Mint a successor and keep the old secret alive for `overlapHours`. Prefer
// this over regenerate whenever more than one consumer holds the key — a CI
// secret store, a laptop keychain and a cron job cannot be updated atomically,
// so they need a window in which both secrets work.
export async function rotateApiKey(
  id: string,
  overlapHours = 24,
): Promise<ApiKeyRotated> {
  const res = await api.post<ApiKeyRotated>(`/v1/api-keys/${id}/rotate`, {
    overlap_hours: overlapHours,
  });
  return res.data;
}
