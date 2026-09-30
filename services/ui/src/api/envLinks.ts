import { api } from "./client";

// Wire types for /api/v1/env-links (Governance › Environments). Mirrors
// services/api/app/schemas/env_link.py.

export type EnvLevel = "account" | "region" | "folder" | "stack";

export type EnvNode = { level: EnvLevel; path: string };

export type RewriteRule = { from: string; to: string; regex: boolean };

export type PairOverrides = {
  pairs: { source: string; target: string }[];
  exclude: { side: "source" | "target"; path: string }[];
};

export type ProtectedRules = { keys: string[]; values: string[] };

export type PairStatus =
  | "not_compared"
  | "in_sync"
  | "diverged"
  | "missing_in_target"
  | "missing_in_source"
  | "excluded";

export type EnvPair = {
  id: string | null;
  relative_path: string;
  source_rel: string | null;
  target_rel: string | null;
  source_stack_id: string | null;
  target_stack_id: string | null;
  source_stack_name: string | null;
  target_stack_name: string | null;
  status: PairStatus;
  reason: string | null;
  proposed_target_rel: string | null;
  summary: Record<string, unknown> | null;
  last_compared_at: string | null;
};

export type PairSummary = Record<PairStatus, number> & { total: number };

export type EnvLink = {
  id: string;
  business_unit_id: string;
  name: string;
  engine: string;
  source_node: EnvNode;
  target_node: EnvNode;
  level: EnvLevel;
  source_account_id: string | null;
  target_account_id: string | null;
  rewrite_rules: RewriteRule[];
  pair_overrides: PairOverrides;
  protected_rules: ProtectedRules;
  rules_version: number;
  created_by: string | null;
  updated_by: string | null;
  created_at: string;
  updated_at: string;
  pair_summary: PairSummary;
  helm_skipped: number;
  warnings: string[];
  last_promotion: null | Record<string, unknown>;
};

export type EnvLinkRules = {
  source_node: EnvNode;
  target_node: EnvNode;
  rewrite_rules: RewriteRule[];
  pair_overrides: PairOverrides;
};

export type EnvLinkInput = EnvLinkRules & {
  name: string;
  protected_rules?: ProtectedRules;
};

export type PairingPreview = {
  level: EnvLevel;
  source_account_id: string | null;
  target_account_id: string | null;
  pairs: EnvPair[];
  summary: PairSummary;
  helm_skipped: number;
  warnings: string[];
  default_protected_rules: ProtectedRules;
};

export const PAIR_STATUS_LABEL: Record<PairStatus, string> = {
  not_compared: "Not compared",
  in_sync: "In sync",
  diverged: "Diverged",
  missing_in_target: "Missing in target",
  missing_in_source: "Missing in source",
  excluded: "Excluded",
};

export const PAIR_STATUS_TONE: Record<PairStatus, "neutral" | "success" | "warning" | "danger" | "violet"> = {
  not_compared: "neutral",
  in_sync: "success",
  diverged: "warning",
  missing_in_target: "danger",
  missing_in_source: "violet",
  excluded: "neutral",
};

/** "28 in sync · 9 diverged · 59 missing in target" — zero buckets omitted. */
export function pairSummaryText(s: PairSummary): string {
  const order: PairStatus[] = [
    "in_sync",
    "diverged",
    "not_compared",
    "missing_in_target",
    "missing_in_source",
    "excluded",
  ];
  const parts = order
    .filter((k) => (s[k] ?? 0) > 0)
    .map((k) => `${s[k]} ${PAIR_STATUS_LABEL[k].toLowerCase()}`);
  return parts.length ? parts.join(" · ") : "No stacks paired";
}

export function extractError(e: unknown, fallback: string): string {
  const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail.map((d: { msg?: string }) => d?.msg ?? String(d)).join("; ");
  }
  return fallback;
}

export async function listEnvLinks(): Promise<EnvLink[]> {
  return (await api.get<EnvLink[]>("/v1/env-links")).data;
}

export async function getEnvLink(id: string): Promise<EnvLink> {
  return (await api.get<EnvLink>(`/v1/env-links/${id}`)).data;
}

export async function listEnvPairs(id: string): Promise<{ items: EnvPair[]; summary: PairSummary; warnings: string[] }> {
  return (await api.get(`/v1/env-links/${id}/pairs`)).data;
}

export async function createEnvLink(body: EnvLinkInput): Promise<EnvLink> {
  return (await api.post<EnvLink>("/v1/env-links", body)).data;
}

export async function updateEnvLink(id: string, body: Partial<EnvLinkInput>): Promise<EnvLink> {
  return (await api.put<EnvLink>(`/v1/env-links/${id}`, body)).data;
}

export async function deleteEnvLink(id: string): Promise<void> {
  await api.delete(`/v1/env-links/${id}`);
}

export async function previewPairs(body: EnvLinkRules): Promise<PairingPreview> {
  return (await api.post<PairingPreview>("/v1/env-links/preview-pairs", body)).data;
}

// ─── compare / promotion (phase 2+) ──────────────────────────────────────────

export type Direction = "forward" | "reverse";

export type HunkClass = "promotable" | "protected" | "backend";
export type HunkCategory = "module" | "inputs" | "providers" | "other";

export type Hunk = {
  id: string;
  file: string;
  key: string;
  category: HunkCategory;
  kind: "added" | "removed" | "changed";
  source_value: string | null;
  target_value: string | null;
  classification: HunkClass;
  protected_reason: string | null;
  applicable: boolean;
  not_applicable_reason: string | null;
  source_lines: [number, number] | null;
  target_lines: [number, number] | null;
  level: "key" | "file";
  /** Hunks sharing a group are applied together (a whole block added/removed). */
  group: string | null;
};

export type FileDiff = {
  path: string;
  status: "added" | "removed" | "modified" | "unchanged";
  unified: string;
  truncated: boolean;
  source_text: string | null;
  target_text: string | null;
};

export type ConfigDiff = {
  files: FileDiff[];
  hunks: Hunk[];
  warnings: string[];
  summary: {
    files_changed?: number;
    keys_changed?: number;
    protected_count?: number;
    backend_count?: number;
    promotable_count?: number;
    module_versions?: { key: string; from: string | null; to: string | null }[];
    providers?: { key: string; from: string | null; to: string | null }[];
    categories?: Record<string, number>;
  };
};

export type StateDiff = {
  source_meta: { serial: number | null; lineage: string | null; terraform_version: string | null; resource_count: number };
  target_meta: { serial: number | null; lineage: string | null; terraform_version: string | null; resource_count: number };
  type_counts: { type: string; source: number; target: number }[];
  only_in_source: string[];
  only_in_target: string[];
  differing: { address: string; type: string; attributes: { key: string; source: string | null; target: string | null }[]; attrs_truncated?: boolean }[];
  in_both: number;
  identical: number;
  truncated?: boolean;
};

export type SideRef = {
  stack_id: string | null;
  stack_name: string | null;
  repo_url: string | null;
  branch: string | null;
  commit: string | null;
  path: string;
  account_id: string | null;
  drift_status: string | null;
  state_serial: number | null;
  state_resources: number | null;
  state_error: string | null;
  error: string | null;
};

export type CreatePreview = {
  path: string;
  repo_url: string | null;
  branch: string | null;
  account_id: string | null;
  files: { path: string; text: string | null; copy: boolean; changes: string[] }[];
  warnings: string[];
  substitutions: { from: string; to: string }[];
};

export type CompareResult = {
  pair_id: string;
  direction: Direction;
  status: "ready" | "computing";
  stale: boolean;
  computed_at: string | null;
  refs: { source: SideRef; target: SideRef; create_preview?: CreatePreview } | null;
  config_diff: ConfigDiff | null;
  state_diff: StateDiff | null;
  error: string | null;
  pair: { status: PairStatus; summary: Record<string, unknown> | null; relative_path: string };
};

export type PromotionSelection = {
  direction: Direction;
  confirm_reverse?: boolean;
  pairs: { pair_id: string; hunk_ids: string[]; create_in_target?: boolean; target_branch?: string | null }[];
  protected_overrides: { hunk_id: string; reason: string }[];
  reason?: string;
  commit_message?: string;
};

export type PromotionPreview = {
  direction: Direction;
  changes: {
    pair_id: string;
    relative_path: string;
    target_stack_id: string | null;
    target_stack_name: string | null;
    target_path: string;
    repo_url: string | null;
    branch: string | null;
    base_commit: string | null;
    create: boolean;
    hunks: Partial<Hunk>[];
    files: { path: string; status: "added" | "removed" | "modified"; unified: string }[];
  }[];
  affected_stacks: number;
  commits: { repo_url: string; branch: string; stacks: number }[];
  blockers: string[];
  warnings: string[];
  changed_hunks: string[];
  stale: boolean;
  commit_message: string;
  selection_hash: string;
};

export type PromotionStatus =
  | "pending" | "committing" | "commit_failed" | "running" | "awaiting_approval"
  | "applying" | "verifying" | "succeeded" | "partially_succeeded" | "failed" | "rejected";

export type StageStatus = "pending" | "running" | "success" | "failed" | "skipped" | "warning";

export type Promotion = {
  id: string;
  number: number;
  link_id: string;
  link_name: string | null;
  kind: "promote" | "revert";
  reverts_promotion_id: string | null;
  direction: Direction;
  initiated_by: string;
  initiated_by_email: string | null;
  reason: string | null;
  status: PromotionStatus;
  error: string | null;
  commit_message: string | null;
  commits: { repo_url: string; branch: string; base_sha: string; sha: string; files: string[]; web_url: string | null }[];
  created_at: string;
  updated_at: string;
  // detail only
  stacks?: {
    promotion_run_id: string;
    pair_id: string | null;
    target_stack_id: string;
    target_stack_name: string | null;
    target_path: string | null;
    created_stack: boolean;
    run_id: string | null;
    run_status: string | null;
    residual: { in_sync: boolean; promotable?: number; protected?: number; keys?: string[]; error?: string | null } | null;
    verified_at: string | null;
    stages: { key: string; label: string; status: StageStatus; detail: string | null }[];
  }[];
  selection?: PromotionSelection;
  reverted_by?: { id: string; number: number } | null;
};

export const PROMOTION_STATUS_TONE: Record<PromotionStatus, "neutral" | "info" | "success" | "warning" | "danger" | "violet"> = {
  pending: "neutral",
  committing: "info",
  commit_failed: "danger",
  running: "info",
  awaiting_approval: "warning",
  applying: "info",
  verifying: "violet",
  succeeded: "success",
  partially_succeeded: "warning",
  failed: "danger",
  rejected: "neutral",
};

export const promotionStatusLabel = (s: PromotionStatus) => s.replace(/_/g, " ");

export type GitWriteState = {
  enabled: boolean;
  token_configured: boolean;
  token_source: "dedicated" | "github" | null;
  token_tail: string | null;
  username: string | null;
  bot_name: string;
  bot_email: string;
};

export type StackIndex = Record<string, { link_id: string; link_name: string; pair_id: string; side: "source" | "target"; status: PairStatus }[]>;

/** 200 → result; 202 → still computing (result may hold the stale one). */
export async function getCompare(pairId: string, direction: Direction = "forward"): Promise<CompareResult> {
  const r = await api.get<CompareResult>(`/v1/env-pairs/${pairId}/compare`, {
    params: { direction },
    validateStatus: (s) => s === 200 || s === 202,
  });
  return r.data;
}

export async function recompare(linkId: string, opts: { pair_ids?: string[]; direction?: Direction; force?: boolean } = {}) {
  return (await api.post<{ queued: number }>(`/v1/env-links/${linkId}/compare`, opts)).data;
}

export async function reloadState(pairId: string, side: "source" | "target") {
  return (await api.post(`/v1/env-pairs/${pairId}/refresh-state`, { side })).data;
}

export async function refreshStateRun(pairId: string, side: "source" | "target"): Promise<{ run_id: string }> {
  return (await api.post(`/v1/env-pairs/${pairId}/refresh-state/run`, { side })).data;
}

export async function getPairHistory(pairId: string): Promise<{ promotions: Promotion[]; runs: { id: string; workspace_id: string; command: string; status: string; branch: string | null; promotion_id: string | null; created_at: string; side: "source" | "target" }[] }> {
  return (await api.get(`/v1/env-pairs/${pairId}/history`)).data;
}

export async function previewPromotion(linkId: string, sel: PromotionSelection): Promise<PromotionPreview> {
  return (await api.post<PromotionPreview>(`/v1/env-links/${linkId}/promotions/preview`, sel)).data;
}

export async function createPromotion(linkId: string, sel: PromotionSelection): Promise<Promotion> {
  return (await api.post<Promotion>(`/v1/env-links/${linkId}/promotions`, sel, {
    validateStatus: (s) => s === 200 || s === 201,
  })).data;
}

export async function listPromotions(linkId?: string, limit = 50): Promise<Promotion[]> {
  return (await api.get<{ items: Promotion[] }>(`/v1/promotions`, { params: { link_id: linkId, limit } })).data.items;
}

export async function getPromotion(id: string): Promise<Promotion> {
  return (await api.get<Promotion>(`/v1/promotions/${id}`)).data;
}

export async function revertPromotion(id: string): Promise<Promotion> {
  return (await api.post<Promotion>(`/v1/promotions/${id}/revert`)).data;
}

export async function getGitWrite(): Promise<GitWriteState> {
  return (await api.get<GitWriteState>("/v1/integrations/git-write")).data;
}

export async function putGitWrite(body: Partial<GitWriteState> & { token?: string; clear_token?: boolean }): Promise<GitWriteState> {
  return (await api.put<GitWriteState>("/v1/integrations/git-write", body)).data;
}

export async function testGitWrite(): Promise<{ enabled: boolean; token_configured: boolean; ok: boolean; repos: { repo_url: string; ok: boolean; can_push: boolean; detail: string | null }[] }> {
  return (await api.post("/v1/integrations/git-write/test")).data;
}

export async function getStackIndex(): Promise<StackIndex> {
  return (await api.get<StackIndex>("/v1/env-links-stack-index")).data;
}
