export interface AuthConfig { mode: string; oidc_enabled: boolean; oidc_issuer?: string | null; cli_loopback?: boolean }
export interface TokenPair { access_token: string; refresh_token: string; token_type?: string }
export interface BusinessUnit { id: string; slug: string; name: string }
export interface Workspace {
  id: string; business_unit_id: string; name: string; environment: string;
  aws_account_id: string; region: string; repo_url?: string | null; tf_working_dir: string;
  repo_ref: string; kind: string; cluster_id?: string | null; tags: Record<string, string>;
  drift_status: string; path_status: string; azure_subscription_id?: string | null;
  gcp_project_id?: string | null; state_backend: string; created_at?: string | null;
}
export type RunStatus = "pending" | "running" | "planning" | "planned" | "awaiting_approval" | "applying" | "applied" | "failed" | "cancelled";
export const TERMINAL_RUN_STATUSES: ReadonlySet<string> = new Set(["planned", "applied", "failed", "cancelled"]);
/** Statuses at which a plan-phase watcher may stop: the plan has landed (planned/failed/cancelled) or is awaiting approval. */
export const PLAN_LANDED_STATUSES: ReadonlySet<string> = new Set([...TERMINAL_RUN_STATUSES, "awaiting_approval"]);
export interface Run {
  id: string; workspace_id: string; command: string; status: RunStatus | string; branch?: string | null;
  triggered_by?: string | null; policy_status?: string; created_at?: string | null;
  started_at?: string | null; completed_at?: string | null;
}
export interface RunStep {
  id: string; run_id: string; position: number; name: string; status: string;
  started_at?: string | null; completed_at?: string | null; duration_seconds?: number | null;
  output?: string | null; summary_json?: string | null;
}
export interface GraphSummary { add?: number; change?: number; destroy?: number; replace?: number }
export interface RunGraph { nodes: unknown[]; edges: unknown[]; summary: GraphSummary }
export interface Branches { source: string; default_branch?: string | null; branches: string[] }
export interface TriggerRunBody { command: "plan" | "apply" | "destroy"; branch?: string }
