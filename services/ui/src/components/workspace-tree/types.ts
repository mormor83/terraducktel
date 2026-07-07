// Shared types for the workspace tree. Kept dependency-free so both the pure
// path helpers and the React components can import them without cycles.

export type Workspace = {
  id: string;
  name: string;
  environment: string;
  region: string;
  aws_account_id: string;
  drift_status: string;
  repo_url?: string | null;
  tf_working_dir?: string;
  repo_ref?: string;
  webhook_enabled?: boolean;
  // 'ok' = path exists in repo at repo_ref. 'orphaned' = path was
  // removed/renamed and the workspace can no longer plan/apply. Surface
  // an amber badge + offer force-delete when orphaned.
  path_status?: "ok" | "orphaned" | "unknown";
  path_status_checked_at?: string | null;
  // Optional override for state-backend creds. Null/undefined = use
  // aws_account_id's creds (the legacy default). Set when the resource
  // account differs from the bucket-owning account — e.g. a Cloudflare
  // workspace (aws_account_id="global") whose state lives in an AWS
  // bucket owned by another account.
  state_aws_account_id?: string | null;
  // Azure subscription this workspace deploys into. When set, the executor
  // injects ARM_* env vars from this subscription's service principal so the
  // azurerm provider authenticates without the Azure CLI. Drives top-level
  // grouping (an Azure subscription gets its own group, like an AWS account).
  azure_subscription_id?: string | null;
  // Workspace kind. "terraform" (default) drives the existing plan/apply
  // pipeline; "helm" reinterprets the same plan|apply|destroy commands as
  // helm diff/upgrade/uninstall against `cluster_id`. Optional on the wire so
  // pre-028 rows (no column yet) still render as terraform.
  kind?: string;
  cluster_id?: string | null;
};

export type Run = {
  id: string;
  workspace_id: string;
  command: string;
  status: string;
  branch?: string | null;
  created_at?: string;
  started_at?: string | null;
  completed_at?: string | null;
};

export type AwsAccountLite = {
  account_id: string;
  name: string;
};

export type AzureSubscriptionLite = {
  // TDT primary key (what `workspace.azure_subscription_id` stores).
  id: string;
  // The Azure subscription GUID (what the `azure/subscription-<guid>/` repo
  // path encodes — used to match path-detected workspaces to a registration).
  subscription_id: string;
  name: string;
};

// Broadcasts an "expand all" / "collapse all" intent from the top of the tree.
// Bumping `version` (with `expand` set) makes every group sync its local `open`
// state to that value via useEffect. Each group still controls its own state
// otherwise — operators can collapse a sub-tree after expanding all without
// losing the rest.
export type ExpandSignal = { version: number; expand: boolean } | null;
