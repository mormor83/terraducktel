# Terraducktel API Reference

Complete endpoint catalog. Everything is HTTP/JSON over `/api/v1`.

## Conventions

- **Base URL:** `/api/v1` for user-facing endpoints, plus a handful of
  state-token-authenticated `/api/v1/state/*`, `/api/v1/internal/*` and
  `/api/v1/webhooks/*` paths used by the executor / drift detector /
  webhook senders.
- **Auth:** Every user-facing endpoint requires a JWT Bearer token
  (`Authorization: Bearer …`) **or an API key** (`Authorization: Bearer tdt_…`,
  see API Keys below). State + internal + webhook endpoints authenticate via the
  `X-Terraducktel-State-Token` header or a webhook HMAC signature instead.
- **Business Unit scoping:** Most user-facing list/write endpoints honor
  `X-Business-Unit: <slug>`. Rules (see `app/auth/bu_context.py`):
  - Superadmin, no header (or header `all`) → all BUs, no filter.
  - Superadmin, header `<slug>` → that BU (404 if it doesn't exist).
  - Member, no header → caller's first BU membership (alphabetical by slug).
  - Member, header `<slug>` → that BU if the caller is a member (403 otherwise).
  - Member, header `all` → 403 (only superadmins can view "all").
  - An **API key** always forces its own bound BU — any `X-Business-Unit`
    header sent alongside a key is ignored.
  - Endpoints noted as `BU-scoped` filter by the resolved scope; endpoints
    noted as `cross-BU` ignore it entirely.
- **Roles:** `viewer < operator < admin`. Endpoints document the minimum
  role required. A handful of routers (Users, API Keys, Business Units) are
  **interactive-only** — they reject API-key callers outright regardless of
  the key's capability tier, because they touch identity/tenancy rather than
  workspace operations.
- **Secrets:** GET endpoints for integrations and credentialed resources
  (AWS accounts, Azure subscriptions, clusters, variables) never return
  plaintext — only `{configured, masked_tail}` shapes and (for some) a
  cached identity (Slack workspace name, GitHub login, etc.).
- **Pagination:** Most list endpoints return the full result set (BU-scoped
  collections are small by design). Two exceptions currently paginate
  explicitly: `GET /inventory/assets` (`limit`/`offset` query params, default
  `limit=200`, max `1000`) and API-key-authenticated automation is expected
  to filter client-side otherwise.

JWT format:
- **Access token:** HS256, default 480min/8h expiry (tunable via
  `auth.access_token_expire_minutes` in Runtime Config), claims: `sub` (user
  id), `email`, `role`, `is_superadmin`, `name`, `type: "access"`.
- **Refresh token:** HS256, default 24h expiry (tunable via
  `auth.refresh_token_expire_hours`), claims: `sub`, `type: "refresh"`.

---

## Auth — `/api/v1/auth`

Local email+password login is always available as a break-glass path, even
when OIDC SSO is the primary provider. OIDC is gated by the `auth.provider`
config key (`local` / `oidc` / `both`).

| Method | Path | Description | Auth |
|---|---|---|---|
| POST | `/auth/token` | Local email+password login. Returns access + refresh tokens. | public |
| GET  | `/auth/config` | Public auth config: `{mode, oidc_enabled, oidc_issuer}`. | public |
| GET  | `/auth/oidc/login` | Begin OIDC login redirect for the configured provider. 404 if OIDC isn't enabled; 503 if enabled but not configured. | public |
| GET  | `/auth/oidc/callback` | OIDC callback target — exchanges the auth code for tokens, upserts the local user, redirects the browser to `/auth/oidc-finish?access_token=…&refresh_token=…`. | public |

**POST /auth/token** body `{"email": "...", "password": "..."}` →
`{"access_token", "refresh_token", "token_type": "bearer"}`. 401 on bad
credentials.

OIDC role mapping (tested against a generic OIDC-compliant IdP) reads a
configurable claim (default group/role claim) and maps it to a TDT role +
`is_superadmin`; see `services/api/app/auth/oidc.py` for the config-key contract.

---

## Users — `/api/v1/users`

Lists users and edits superadmin status / per-Business-Unit memberships.
**Interactive-only** — the whole router rejects API-key callers, even
`admin`-tier ones, since identity management must never be automatable.

| Method | Path | Description | Min role |
|---|---|---|---|
| GET | `/users` | List all users with their per-BU memberships. | admin |
| GET | `/users/eligible-reviewers` | _Deprecated_ — 4-eyes was removed. Always returns `[]`. | operator |
| PATCH | `/users/{user_id}` | Update a user's `is_superadmin` flag and/or BU memberships. | superadmin |

**PATCH /users/{user_id}** body:
```jsonc
{
  "is_superadmin": true,                 // optional
  "add_memberships": [                    // optional
    {"business_unit_id": "<bu-id>", "role": "operator"}   // role: operator | viewer
  ],
  "remove_memberships": ["<bu-id>"]        // optional, list of business_unit_id
}
```
Requires the caller to already be a superadmin (checked against
`current_user.is_superadmin`, independent of the `admin` role on `/users`
GET). Demoting the last remaining superadmin is rejected with 409. Adding a
membership for a BU that doesn't exist is a 400; adding a membership that
already exists just updates its role.

Response (`UserResponse`): `{id, email, role, auth_provider, external_id,
is_superadmin, memberships: [{business_unit_id, business_unit_slug,
business_unit_name, role}]}`.

---

## API Keys — `/api/v1/api-keys`

Long-lived, scoped credentials for automation. Admin-only to manage; the key
itself authenticates as its owner but is capped at its capability tier and
(optionally) a workspace allowlist, and is pinned to one BU. **Interactive-only**
router — an API key can never mint, rotate, or revoke API keys, even at the
`admin` tier (privilege-escalation guard).

| Method | Path | Description | Min role |
|---|---|---|---|
| GET | `/api-keys` | List keys in the current BU (masked — `token_prefix`, never the token). | admin |
| POST | `/api-keys` | Mint a key. Returns the plaintext `token` **once**. | admin |
| POST | `/api-keys/{id}/regenerate` | Rotate an active key's secret in place, keeping name/capability/allowlist/expiry. Returns the new plaintext `token` **once**. | admin |
| DELETE | `/api-keys/{id}` | Soft-revoke a key (immediate 401 for further use). Idempotent. | admin |

**POST /api-keys** body:
```jsonc
{
  "name": "ci-deploy-bot",
  "capability": "plan",            // read | plan | apply | admin
  "workspace_ids": ["<ws-id>"],    // optional allowlist; omit/null = whole BU
  "expires_at": "2026-12-31T00:00:00Z"  // optional; omit = never
}
```
→ `{ ...key, "token": "tdt_…" }` (the only response that carries the plaintext).
Requires a concrete `X-Business-Unit` — 400 if scoped to "all". A non-empty
`workspace_ids` is validated against the current BU; any id not in this BU
→ 400. `expires_at` must be in the future or 400.

**POST /api-keys/{id}/regenerate** — mints a fresh secret for an existing key
row, clears `last_used_at`, and keeps everything else (name, capability,
workspace allowlist, expiry) unchanged. The old token stops working
immediately. Rejects with **409** if the key is already revoked or has
already expired — rotation only ever refreshes a *live* secret; create a new
key instead of trying to revive a dead one.

**DELETE /api-keys/{id}** — sets `revoked_at`; calling it again on an
already-revoked key is a no-op 200 (idempotent), not a 404/409.

**Using a key:** send `Authorization: Bearer tdt_…`. The key forces its own BU
(any `X-Business-Unit` header is ignored). Capability gates:
- `read` — viewer reads.
- `plan` — trigger plan-only runs (+cancel).
- `apply` — also trigger apply/destroy + approve/reject.
- `admin` — full admin **within the BU**: everything `apply` can do **plus**
  workspace create / discover / import / update / delete, AWS accounts, Azure
  subscriptions, clusters, policies, drift, integrations, variables. Use this
  for CLI-driven workspace discovery and onboarding. ⚠️ Powerful — treat the
  token like an admin password.

**Always interactive-only, even for an `admin` key:** minting/rotating/revoking
API keys, user management, and Business-Unit create/update. A key is bound to
one BU and can never act across BUs. Requests outside the workspace allowlist
→ 403; revoked/expired keys → 401.

---

## Policies — `/api/v1/policies`

BU-scoped OPA/conftest rego rules. The executor enforces them (see the OPA
policy gate in `/integrations/opa`); these endpoints author + test them. Every
write snapshots into `policy_versions` and is audit-logged.

| Method | Path | Description | Min role |
|---|---|---|---|
| GET | `/policies` | List the BU's policies. | viewer |
| POST | `/policies` | Create (snapshots v1). Body: `{name, rego, description?, tests_rego?, severity?, enabled?}`. | admin |
| POST | `/policies/test` | Dry-run vs a plan. Body: `{run_id\|plan_json, rego?, rego_name?, rego_severity?, policy_ids?}`. → `{ok, violations[], warnings[], engine_error?}`. | operator |
| POST | `/policies/verify` | Run rego unit tests. Body: `{rego, tests_rego}`. → `{ok, passed, failures[], engine_error?}`. | operator |
| GET | `/policies/{id}` | Policy detail (full rego). | viewer |
| PUT | `/policies/{id}` | Edit (bumps version). Body: any subset of the create fields. | admin |
| DELETE | `/policies/{id}` | Delete the policy + its history. | admin |
| GET | `/policies/{id}/versions` | Revision list (newest first). | viewer |
| POST | `/policies/{id}/versions/{v}/restore` | Restore a revision into a new current version. | admin |

`severity` is `block` (fails the run under enforce mode) \| `warn` \| `info`.
`POST /policies/test` requires exactly one plan source (`run_id` or `plan_json`);
the policy set is the candidate `rego` and/or `policy_ids`, else all enabled
policies in the BU. Every write requires a concrete `X-Business-Unit` — 400 if
scoped to "all".

Executor-facing: `GET /api/v1/runs/{run_id}/policies` (operator) returns the
merged gate config + enabled policies for the run's BU — see [Runs](#runs--apiv1runs--apiv1workspacesidruns).

---

## Business Units — `/api/v1/business-units`

Multi-tenancy root. **Interactive-only** router (API keys are bound to a
single BU and can never touch this one, regardless of capability tier).

| Method | Path | Description | Min role |
|---|---|---|---|
| GET | `/business-units` | List BUs the caller is a member of (superadmin sees all; an API-key caller sees only its own bound BU). | any authenticated caller |
| POST | `/business-units` | Create a BU. `{slug, name}`. 409 if the slug already exists. | superadmin |
| PUT | `/business-units/{bu_id}` | Update a BU's `name` (slug is immutable post-create). | superadmin |

`slug` must match `^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$` (lowercase
letters/digits/hyphens, 3-64 chars, no leading/trailing hyphen).

---

## AWS Accounts — `/api/v1/aws-accounts`

Encrypted-at-rest AWS credentials, one row per account per BU.

| Method | Path | Description | Min role | BU |
|---|---|---|---|---|
| GET | `/aws-accounts` | List configured AWS accounts (masked). | viewer | BU-scoped |
| POST | `/aws-accounts` | Add an AWS account (creds stored encrypted). | admin | BU-scoped |
| PUT | `/aws-accounts/{account_pk}` | Update AWS account creds / display name. | admin | — |
| DELETE | `/aws-accounts/{account_pk}` | Delete an AWS account row. | admin | — |
| POST | `/aws-accounts/{account_pk}/test` | Probe creds via STS `GetCallerIdentity` + `s3:HeadBucket`. | admin | — |
| POST | `/aws-accounts/{account_pk}/bucket` | Create the account's S3 state bucket if missing (idempotent). | admin | — |

**POST /aws-accounts** body: `{account_id (12-digit), name, description?,
state_bucket, state_bucket_region?, default_region?, aws_profile_name?,
access_key_id, secret_access_key}`. Requires a concrete `X-Business-Unit` —
400 if scoped to "all". `account_id` is unique **per BU** (the same AWS
account number may legitimately be registered in two different BUs) — a
duplicate within the same BU is **409**.

`POST .../bucket` hardens a freshly created bucket with versioning, AES256
default encryption, and a public-access block; response carries
`already_existed` so callers can tell a no-op from a real creation.
`{account_pk}` in the path is the TDT row id (not the 12-digit AWS account
number).

---

## Azure Subscriptions — `/api/v1/azure-subscriptions`

Encrypted-at-rest Azure Service Principal credentials, mirroring AWS Accounts.
Workspaces that target `azurerm` link one of these; the executor exports
`ARM_*` env vars from the decrypted SP secret at run time.

| Method | Path | Description | Min role | BU |
|---|---|---|---|---|
| GET | `/azure-subscriptions` | List configured subscriptions (masked). | viewer | BU-scoped |
| POST | `/azure-subscriptions` | Add a subscription (SP secret stored encrypted). | admin | BU-scoped |
| PUT | `/azure-subscriptions/{sub_pk}` | Update name/description/location or rotate the client secret. | admin | — |
| DELETE | `/azure-subscriptions/{sub_pk}` | Delete a subscription row. | admin | — |
| POST | `/azure-subscriptions/{sub_pk}/test` | Validate the SP creds via an ARM OAuth2 token; also probes the Blob state container when configured. | admin | — |
| POST | `/azure-subscriptions/{sub_pk}/container` | Create (or verify) the Blob state container using the SP. | admin | — |

**POST /azure-subscriptions** body: `{subscription_id, tenant_id, client_id
(all UUIDs), client_secret, name, description?, default_location?,
state_storage_account?, state_container?}`. Requires a concrete
`X-Business-Unit`. `subscription_id` is unique per BU — duplicate → **409**.

`{sub_pk}` in the path is the TDT row id (not the Azure subscription GUID).
Workspaces under a Git-synced repo at `azure/subscription-<guid>/<region>/<stack>`
auto-link to the matching subscription by GUID on import — no manual picker
needed if the subscription is already registered.

Set `state_storage_account` + `state_container` to enable **Azure Blob** as a
Terraform state backend for workspaces flagged `state_backend=azureblob` (state
is written via the same SP over AAD — grant it *Storage Blob Data Contributor*).

---

## GCP Projects — `/api/v1/gcp-projects`

Encrypted-at-rest GCP service-account keys, mirroring AWS Accounts / Azure
Subscriptions. Workspaces that target the `google` provider link one of these;
the executor writes the SA-key JSON to a 0600 file and exports
`GOOGLE_APPLICATION_CREDENTIALS` / `GOOGLE_PROJECT` / `GOOGLE_REGION` at run time.

| Method | Path | Description | Min role | BU |
|---|---|---|---|---|
| GET | `/gcp-projects` | List configured projects (SA key never returned; email shown). | viewer | BU-scoped |
| POST | `/gcp-projects` | Add a project (SA-key JSON stored encrypted). | admin | BU-scoped |
| PUT | `/gcp-projects/{project_pk}` | Update name/description/region/state bucket or rotate the SA key. | admin | — |
| DELETE | `/gcp-projects/{project_pk}` | Delete a project row. | admin | — |
| POST | `/gcp-projects/{project_pk}/test` | Validate the SA key by minting an access token (google-auth). | admin | — |
| POST | `/gcp-projects/{project_pk}/bucket` | Create (or verify) the GCS state bucket using the SA key. | admin | — |

**POST /gcp-projects** body: `{project_id, name, description?, default_region?,
state_bucket?, state_prefix?, service_account_json}`. The JSON is validated
structurally (must be a `service_account` key) and its embedded `project_id`
must match — mismatch → **422**. `project_id` is unique per BU — duplicate → **409**.
Set `state_bucket` to enable **GCS** as a Terraform state backend for workspaces
flagged `state_backend=gcs`. Workspaces at `gcp/project-<id>/<region>/<stack>`
auto-link to the matching project on import.

---

## Kubernetes Clusters — `/api/v1/clusters`

Encrypted-at-rest kubeconfigs for Helm-kind workspaces
(`workspace.kind=helm`). Mirrors AWS Accounts: BU-scoped CRUD +
connectivity test. The kubeconfig is **never** returned or logged — responses
carry only a masked tail.

| Method | Path | Description | Min role | BU |
|---|---|---|---|---|
| GET | `/clusters` | List clusters (masked). | viewer | BU-scoped |
| POST | `/clusters` | Register a cluster (kubeconfig stored encrypted). | admin | BU-scoped |
| PUT | `/clusters/{cluster_id}` | Update name/description/namespace, relink AWS account, or rotate the kubeconfig. | admin | BU-scoped |
| DELETE | `/clusters/{cluster_id}` | Delete a cluster row. | admin | BU-scoped |
| POST | `/clusters/{cluster_id}/test` | Validate connectivity via `kubectl version`. | admin | BU-scoped |

**POST /clusters** body: `{name, description?, server_url?,
default_namespace?, aws_account_id?, kubeconfig}`. `aws_account_id` is
optional — set it for EKS clusters whose kubeconfig authenticates via the
`aws eks get-token` exec plugin; the test/executor paths then export that
account's decrypted creds (+ its `default_region`) into the subprocess
environment. Leave null for non-EKS clusters.

`POST .../test` writes the decrypted kubeconfig to a private 0600 temp file,
runs `kubectl version --request-timeout=8s`, and always deletes the temp file
afterward — the kubeconfig itself is never echoed back in the result, only
`{ok, detail?, context?}` (the resolved `kubectl config current-context`).

---

## Workspaces — `/api/v1/workspaces`

A workspace is one Terraform state (or one Helm release) plus its Git-tracked
source path. `kind` is `terraform` (default) or `helm` — Helm workspaces
target a `cluster_id` instead of an `aws_account_id` and skip the S3/HTTP
state backend entirely (Helm release state lives in-cluster).

`state_backend` (`s3` default | `azureblob` | `gcs`) selects where Terraform
state is stored. `azureblob` requires a linked `azure_subscription_id` whose
`state_storage_account`/`state_container` are set; `gcs` requires a linked
`gcp_project_id` whose `state_bucket` is set — create/update **422** otherwise.
`gcp_project_id` links the workspace to a GCP project (google provider), the
mirror of `azure_subscription_id`.

| Method | Path | Description | Min role | BU |
|---|---|---|---|---|
| GET | `/workspaces` | List workspaces. | viewer | BU-scoped |
| GET | `/workspaces/{id}` | Get a single workspace. | viewer | BU-scoped |
| POST | `/workspaces` | Create a workspace (manual). | admin | BU-scoped |
| PUT | `/workspaces/{id}` | Update workspace (branch override, drift settings, `state_aws_account_id`, `azure_subscription_id`, `gcp_project_id`, `state_backend`, …). | admin-tier key or interactive operator+ | BU-scoped |
| POST | `/workspaces/discover` | Enumerate importable paths in a Git repo or local mount. | admin | BU-scoped |
| POST | `/workspaces/import` | Bulk-import workspaces from a discovery result. | admin | BU-scoped |
| GET | `/workspaces/{id}/branches` | List GitHub branches for the workspace's repo (falls back to free text if no token / non-GitHub remote). | viewer | BU-scoped |
| POST | `/workspaces/{id}/sync` | Re-check a single workspace's path against its tracked ref now. | admin | BU-scoped |
| POST | `/workspaces/sync` | Re-check every workspace's path in the current BU. | admin | BU-scoped |
| GET | `/workspaces/{id}/state-lock` | Report whether a terraform state lock is currently held, and by which run. | viewer | BU-scoped |
| DELETE | `/workspaces/{id}/state-lock` | Force-release a stuck state lock. Audited (`workspace.force_unlock`). | admin-tier key or interactive operator+ | BU-scoped |
| DELETE | `/workspaces/{id}` | Delete a workspace. Rejects git-synced ones with 409 unless `?force=true`. With `?delete_state=true`, also removes the S3 tfstate (default: retain). | admin | BU-scoped |

Note on `PUT` and the state-lock `DELETE`: the route itself only requires
`operator`+ for interactive JWT callers, but both are additionally gated via
`api_key_service.enforce(..., need="admin", ...)`, which is a no-op for JWT
callers and requires the `admin` capability tier for API-key callers. In
practice: any interactive operator/admin can call them; an automation token
needs the `admin` tier.

### POST /workspaces — body

```jsonc
{
  "name": "network-vpc",
  "environment": "prod",
  "aws_account_id": "111111111111",   // required unless kind="helm"
  "region": "us-east-1",
  "repo_url": "https://github.com/example-org/infra.git",  // optional; local:// or omit for manual
  "tf_working_dir": ".",
  "repo_ref": "main",
  "webhook_enabled": false,
  "kind": "terraform",                // "terraform" | "helm"
  "cluster_id": null,                 // required for kind="helm"
  "azure_subscription_id": null       // optional Azure link
}
```
For `kind="helm"`, `cluster_id` must reference a cluster in the same BU (400
otherwise) and `aws_account_id` defaults to the `"global"` sentinel if
omitted. For `kind="terraform"`, `aws_account_id` is required and must
reference an AWS account already registered in the same BU (400 otherwise).
An optional `azure_subscription_id` must belong to the same BU if set (400
otherwise).

A workspace is unique per BU on `(aws_account_id, region, environment,
tf_working_dir)` — violating that identity tuple on create or update returns
a clean **409**, not a raw 500.

### POST /workspaces/discover — body

```jsonc
{
  "repo_url": "https://github.com/example-org/infra.git",  // or local_path (dev-only)
  "ref": "main",
  "username": "x-access-token",   // optional; falls back to configured GitHub PAT
  "token": "ghp_..."               // optional; falls back to Settings → GitHub
}
```
Returns `{repo_url, ref, accounts: [{aws_account_id, regions: {<region>:
[{path, name, aws_account_id, region, suggested_environment, has_tf, kind,
already_imported}]}}], stack_count, errors}`. Each leaf folder containing
`*.tf` (or a Helm `Chart.yaml`) becomes a candidate. `already_imported` flags
paths that are already workspaces in the current BU so the UI can gray them
out.

### POST /workspaces/import — body

```jsonc
{
  "repo_url": "https://github.com/example-org/infra.git",
  "ref": "main",
  "entries": [
    {"path": "account-111111111111/us-east-1/network/prod", "name": "network",
     "aws_account_id": "111111111111", "region": "us-east-1", "environment": "prod",
     "kind": "terraform", "cluster_id": null}
  ]
}
```
→ `{created: [WorkspaceResponse, ...], skipped: [{path, reason}, ...]}`.
Duplicates (same path within the BU) are skipped, not errored. Azure leaves
at `azure/subscription-<guid>/…` auto-link to a matching subscription in the
BU by GUID; an unmatched GUID just leaves the workspace unlinked.

**Orphan handling** — `WorkspaceResponse.path_status` is `ok` / `orphaned` /
`unknown`. A background loop in the API process (default 10 min,
configurable via `repo_sync.poll_seconds` in Runtime Config) shallow-clones
each workspace's tracked ref and flips the status to `orphaned` when
`tf_working_dir` is missing. The UI shows an amber badge and replaces
Run/Destroy with a "Force delete" button on those rows. `?force=true` plus
optional `?delete_state=true` on the DELETE is the path for cleaning up the
TDT row when the source path was renamed/removed; the actual cloud resources
are presumed to have been migrated to the new workspace (or destroyed by
hand) — there is no fallback that runs a real terraform destroy on an orphan.
A force-delete of a git-synced workspace is audited as
`workspace.force_delete`.

**State-backend cred override (`state_aws_account_id`)** — optional column on
`Workspace`. `aws_account_id` always means "the AWS account whose resources
this workspace manages" and is what the dashboard groups by; it's also what
the executor uses by default to look up per-account creds for
`AWS_ACCESS_KEY_ID`. For non-AWS workspaces (`aws_account_id="global"`) whose
terraform state nonetheless lives in an AWS S3 bucket owned by a different
account, set `state_aws_account_id` to that account's id — the executor uses
THAT account's per-account creds for the state backend while the workspace
stays grouped under "global" in the tree. `PUT /workspaces/{id}` accepts the
field; the new value (when non-empty) must be a registered AWS account in the
workspace's BU or the call **422**s. Pass empty string to clear back to "same
as aws_account_id".

**PUT /workspaces/{id}** — passing `{"repo_ref": "feat/x"}` changes the
tracked branch atomically. Useful for the `branch_only` flow. `azure_subscription_id`
follows the same empty-string-clears / value-relinks convention as
`state_aws_account_id`.

**State lock inspection/release** — `GET .../state-lock` → `{held, run_id?,
acquired_at?}`. `DELETE .../state-lock` force-releases a Postgres advisory
lock stuck from a crashed executor; use only when certain no executor is
actually running against the workspace, since releasing a live lock could let
a second concurrent run race state.

---

## Workspace variables — `/api/v1/workspaces/{id}/variables`

| Method | Path | Description | Min role |
|---|---|---|---|
| GET | `…/variables` | List workspace-scoped variables (secrets masked). | viewer |
| POST | `…/variables` | Create a workspace variable. 409 if the key already exists on this workspace. | operator |
| PATCH | `…/variables/{var_id}` | Update a workspace variable. | operator |
| DELETE | `…/variables/{var_id}` | Delete a workspace variable. | operator |

## Global variables — `/api/v1/variables`

| Method | Path | Description | Min role |
|---|---|---|---|
| GET | `/variables` | List global variables (BU-scoped; secrets masked). | viewer |
| POST | `/variables` | Create a global variable. 409 if the key already exists. | admin |
| PATCH | `/variables/{var_id}` | Update. | admin |
| DELETE | `/variables/{var_id}` | Delete. | admin |

Both routers share the same request/response shape (`VariableCreate` /
`VariableUpdate` / `VariableResponse`):
```jsonc
// create
{"key": "instance_type", "value": "t3.medium", "is_secret": false, "is_hcl": false, "description": "..."}
```
`key` must match `^[A-Za-z_][A-Za-z0-9_]*$` (Terraform identifier rules) — a
mismatch is a 422 before anything is encrypted or written. `key` is immutable
after creation (delete + recreate to rename). Response: non-secret rows
return the real `value`; secret rows always return `value: null` plus
`masked_tail` (e.g. `…ab12`) so the UI can confirm which secret is configured
without ever re-exposing it. `PATCH` with `value` omitted leaves the stored
ciphertext untouched — use it to rotate `description`/`is_secret` without
re-supplying the value.

Variable precedence at run time: `global ← workspace ← run` (last wins
per key).

---

## Runs — `/api/v1/runs` (+ `/api/v1/workspaces/{id}/runs`)

| Method | Path | Description | Min role |
|---|---|---|---|
| POST | `/workspaces/{id}/runs` | Trigger a run (plan/apply/destroy). | operator (+ `plan` or `apply` API-key tier) |
| GET | `/runs` | List runs (BU-scoped; API-key callers additionally narrowed to their workspace allowlist). | viewer |
| GET | `/runs/{run_id}` | Get a run. | viewer |
| PATCH | `/runs/{run_id}` | Executor callbacks — status, plan output, plan_json, tfplan_b64, policy_status. Triggers notifications + auto-approve gate on `awaiting_approval`. | operator |
| POST | `/runs/{run_id}/heartbeat` | Executor liveness ping (run_jobs.heartbeat_at). 204, or 404 if there's no picked job for this run. | operator |
| POST | `/runs/{run_id}/cancel` | Cancel a run that hasn't reached `applying` yet. | operator (+ `plan` API-key tier) |
| GET | `/runs/{run_id}/plan` | Raw plan output: `{plan_output}`. | viewer (+ `read` API-key tier) |
| GET | `/runs/{run_id}/tfplan` | Base64-encoded `tfplan` binary (executor uses this on apply): `{tfplan_b64}`. | operator |
| GET | `/runs/{run_id}/graph` | Structured `{nodes, edges, summary}` parsed from plan_json. | viewer (+ `read` API-key tier) |
| GET | `/runs/{run_id}/steps` | Run timeline (Init / Checkov / Plan / etc). | viewer (+ `read` API-key tier) |
| PATCH | `/runs/{run_id}/steps/{step_id}` | Executor callback to update a single step. | operator |
| GET | `/runs/{run_id}/policies` | OPA policy bundle + gate config the executor should enforce for this run. BU is derived from the run's workspace. | operator |

### POST /workspaces/{id}/runs — body

```jsonc
{
  "command": "plan" | "apply" | "destroy",   // default "plan"
  "variables": [{"key": "...", "value": "...", "is_secret": false, "is_hcl": false}],  // per-run additions, optional
  "branch": "feat/x",                         // optional override, persisted to workspace.repo_ref
  "auto_approve_if_no_changes": false,        // see auto-approve section
  "auto_approve_skip_apply": false
}
```
Response is a `RunResponse`: `{id, workspace_id, triggered_by, reviewer_id,
command, status, branch, plan_output, error_output, policy_status,
created_at, started_at, completed_at, auto_approve_if_no_changes,
auto_approve_skip_apply}`.

### Run status machine

`pending → planning → planned → awaiting_approval → applying → applied`, with
`failed` / `cancelled` reachable from most non-terminal states. Notable
guardrails enforced by `PATCH /runs/{run_id}`:
- A PATCH cannot move a run directly into `applying` — only `POST
  /runs/{id}/approve` may do that.
- `applied` is only accepted immediately after `applying`.
- A PATCH that repeats the run's current status is a no-op (idempotent — lets
  a reconnecting executor retry a heartbeat-adjacent status write safely).
- Cancellable source states: `pending`, `running`, `planning`, `planned`,
  `awaiting_approval`. Once `applying`, a run must complete or fail — it can
  no longer be cancelled (409 on `POST /cancel`).

### Auto-approve flow

When `auto_approve_if_no_changes` is `true` on an apply/destroy run AND
the plan succeeds with `Checkov` + `Cost` gates green AND
`plan_json.resource_changes` summary is 0/0/0, the API:

1. Posts a synthetic system approval (audit `action="auto_approve"`,
   `user_id=null`, details include the plan summary).
2. If `auto_approve_skip_apply` is `true`, also writes
   `action="auto_apply_skipped"` and transitions
   `awaiting_approval → applying → applied` without spawning an executor.
3. Otherwise enqueues an `apply` job exactly like a human approval would.
4. Posts a Slack notification if the BU has Slack integration configured.

The flag is silently ignored when `command="plan"` (no apply phase to
approve). All four Slack notification kinds (auto-approved, awaiting
approval, run failed, drift detected) require a configured Slack integration
on the target workspace's BU — failures are best-effort and never roll back
the run state machine.

---

## Approvals — `/api/v1/runs/{run_id}` (approve/reject)

| Method | Path | Description | Min role |
|---|---|---|---|
| POST | `/runs/{run_id}/approve` | Approve a run awaiting approval; queues the apply phase. | operator (+ `apply` API-key tier) |
| POST | `/runs/{run_id}/reject` | Reject a run awaiting approval; transitions to cancelled. | operator (+ `apply` API-key tier) |

Body (optional): `{"comment": "...optional..."}`. 4-eyes approval was
revoked — the triggering user may also approve their own run. Any operator+
in the run's BU can approve or reject any run in that BU.

---

## Drift — `/api/v1/drift`

Per-workspace and per-BU drift summaries, plus report ingestion from the
drift-detector service.

| Method | Path | Description | Min role |
|---|---|---|---|
| GET | `/drift/summary` | Per-BU drift breakdown: latest report per workspace, aggregated. | viewer |
| GET | `/drift/{workspace_id}` | Latest drift report for one workspace, with per-resource detail. | viewer |
| POST | `/drift/{workspace_id}/scan` | Queue an on-demand drift scan; returns a placeholder report id immediately (the real detector runs async). | admin |
| POST | `/drift/{workspace_id}/report` | Submit drift scan results (detector or manual/test). | admin |

Reports are also accepted at the internal, state-token-authenticated path
`POST /api/v1/internal/drift/{workspace_id}/report` (used by the real
drift-detector service) and trigger a Slack alert whenever `has_drift=true`.
Either path additionally refreshes the cloud-asset Inventory when the report
includes an `assets[]` payload.

**POST /drift/{workspace_id}/report** body (`DriftReportIn`):
```jsonc
{
  "workspace_id": "<id>",
  "has_drift": true,
  "summary": "3 resources modified out of band",
  "plan_output": "...",
  "modified_count": 3, "untracked_count": 0, "deleted_count": 0, "mismatch_count": 0,
  "resources": [{"address": "aws_s3_bucket.logs", "type": "aws_s3_bucket", "provider": "aws", "drift_type": "modified", "summary": "..."}],
  "assets": []   // optional; see Inventory
}
```
`drift_type` is one of `modified` \| `untracked` \| `deleted` \| `mismatch`.
Posting a report also updates `workspace.drift_status` to `drifted` or
`clean`.

---

## Inventory — `/api/v1/inventory`

Firefly-style cloud asset inventory: a read-side view of every discovered
cloud resource and how well Infrastructure-as-Code tracks it. Backed by the
`cloud_assets` table, which the drift-detector refreshes on every scan via
the `assets[]` field of a drift report.

| Method | Path | Description | Min role |
|---|---|---|---|
| GET | `/inventory/summary` | Headline KPIs: codification %, per-`iac_status` counts, filter facets. | viewer |
| GET | `/inventory/assets` | Filterable, paginated asset list. | viewer |
| GET | `/inventory/ignore-rules` | List the current BU's ignore rules. | viewer |
| POST | `/inventory/ignore-rules` | Add an ignore rule; immediately reclassifies matching assets to `ignored`. | admin |
| DELETE | `/inventory/ignore-rules/{rule_id}` | Delete an ignore rule (suppressed assets revert on the next scan). | admin |

`iac_status` is one of `codified` \| `drifted` \| `ghost` \| `unmanaged` \|
`ignored` \| `undetermined`. Codification % = tracked-by-IaC ÷ (total minus
`ignored`/`service_managed`), rounded to the nearest integer.

**GET /inventory/summary** query params (all optional, apply to the
counts/codification but not to the facets, which stay BU-global so filter
dropdowns never empty out): `provider`, `region`, `account_id`, `search`
(substring match on asset id/address).

**GET /inventory/assets** — same scope filters plus `iac_status`,
`asset_type`, and pagination: `limit` (default 200, max 1000), `offset`
(default 0). Returns `{total, items: [...]}`.

**POST /inventory/ignore-rules** body: `{match_type: "arn_glob" |
"asset_type", pattern, note?}`. Requires a concrete `X-Business-Unit` — 400
if scoped to "all".

---

## Audit — `/api/v1/audit`

Tamper-evident hash-chained log of every write in the system.

| Method | Path | Description | Min role |
|---|---|---|---|
| GET | `/audit/verify` | Re-walk the audit hash chain and report integrity. | admin |
| GET | `/audit` | List audit entries, optionally filtered by `run_id` and/or `workspace_id`. | admin |

**GET /audit/verify** query: `limit` (optional, 1-100000). Returns `{ok:
true, total: N, broken_at: []}` on a clean chain, or the first few offending
row ids if a stored hash fails to reproduce — that's the breakpoint for
forensic review.

**GET /audit** — cross-BU (no `X-Business-Unit` filtering is applied); the
only supported filters today are `run_id` and `workspace_id` query params.
Response: `{items: [{id, user_id, action, resource_type, resource_id,
workspace_id, details, created_at}, ...]}`.

---

## Presence — `/api/v1/presence`

Cross-BU "who's online" indicator for the top bar. The UI pings every ~30s
with the BU slug it currently has selected; the read side returns everyone
seen in the last 60 seconds, deliberately **across every BU** — the entire
point is letting an operator in one BU notice someone deploying in another
and avoid a concurrent-change collision. Only identity (email, display name,
selected BU slug) is exposed — never page contents or run state.

| Method | Path | Description | Auth |
|---|---|---|---|
| POST | `/presence` | Upsert the caller's presence row. Body: `{bu_slug?}`. 204. | any authenticated user |
| GET | `/presence` | List users active in the last 60s, across all BUs. | any authenticated user |

Presence rows older than ~10× the window are lazily garbage-collected on each
GET.

---

## Environments — `/api/v1/environments`

| Method | Path | Description | Min role |
|---|---|---|---|
| GET | `/environments` | List every workspace grouped by environment stage (`dev`/`staging`/`prod`), plus the promotion order. Cross-BU — no `X-Business-Unit` filtering. | viewer |
| POST | `/environments/{workspace_id}/promote` | Promote a workspace's config to the next environment stage and trigger a plan run there. | admin |

Promotion chain is fixed: `dev → staging → prod`. 400 if the source
workspace's environment isn't in the chain; 409 if it's already at `prod`
(the final stage). Promoting creates (or reuses, if already present) a
sibling workspace with the same name in the next stage, copying `repo_url`,
`repo_ref`, `tf_working_dir`, `kind`, `cluster_id`, `azure_subscription_id`,
and `state_aws_account_id` — but deliberately **not** `state_key`,
`webhook_enabled`, or any drift/path status, so the new environment gets its
own state path and starts with webhooks off and no scan history.

---

## Integrations — `/api/v1/integrations`

Per-BU third-party credentials and pipeline-gate configuration, stored in the
encrypted `config` table. All admin-write / viewer-or-admin-read, BU-scoped
via `X-Business-Unit` (every route 400s without a concrete BU). Most
sub-resources fall back to a legacy global (non-BU) config key for one
release if the BU hasn't saved its own value yet — the response's `inherited`
flag tells the UI which case it's in.

### GitHub

| Method | Path | Description |
|---|---|---|
| GET | `/integrations/github` | `{configured, token_tail, overridden_by_env, inherited}` |
| PUT | `/integrations/github` | Set GitHub PAT. Body: `{"token": "ghp_…"}`. |
| DELETE | `/integrations/github` | Remove the BU's saved token. |
| POST | `/integrations/github/test` | Probe the token against `/user`; returns `{ok, login, scopes}`. |

### GitHub webhook (per-BU secret + org binding)

| Method | Path | Description |
|---|---|---|
| GET | `/integrations/webhook` | `{bu_slug, configured, secret_tail, github_org, webhook_path}` |
| PUT | `/integrations/webhook` | Body: `{secret?, github_org?}`. |

### Infracost (cost estimation)

| Method | Path | Description |
|---|---|---|
| GET | `/integrations/infracost` | `{configured, api_key_tail, currency, overridden_by_env, inherited}` |
| PUT | `/integrations/infracost` | Body: `{api_key?, currency?}`. Empty-string `api_key` removes it. |
| POST | `/integrations/infracost/test` | Pings the Infracost pricing GraphQL endpoint; returns `{ok, detail?, organization?}`. |

### Checkov gate

| Method | Path | Description |
|---|---|---|
| GET | `/integrations/checkov` | `{mode: "fail"\|"warn", inherited}` |
| PUT | `/integrations/checkov` | Body: `{mode}`. |

### OPA policy gate

| Method | Path | Description |
|---|---|---|
| GET | `/integrations/opa` | `{mode: "enforce"\|"warn"\|"off", use_bundled, bundled_severity, git_severity, repo_url, repo_ref, repo_dir, inherited}` |
| PUT | `/integrations/opa` | Body: same shape (admin). `mode` defaults to `off`. |

### Terraform modules registry

| Method | Path | Description |
|---|---|---|
| GET | `/integrations/modules` | `{mode: "github"\|"local", upstream_url, local_host_dir, inherited}` |
| PUT | `/integrations/modules` | Body: same shape. `local` mode requires non-empty `local_host_dir` (422 otherwise). |

### Default infra repo (Git-import prefill)

| Method | Path | Description |
|---|---|---|
| GET | `/integrations/infra-repo` | `{repo_url, inherited}` — not a secret, just a UI convenience default for the Discover form. |
| PUT | `/integrations/infra-repo` | Body: `{repo_url}`. |

### Changelog (GitHub Releases / merged PRs feed)

| Method | Path | Description | Min role |
|---|---|---|---|
| GET | `/integrations/changelog` | `{repo, configured, inherited}` — the `owner/repo` the Changelog tab reads from. | viewer |
| PUT | `/integrations/changelog` | Body: `{repo}` in `owner/repo` form (422 on a malformed value). | admin |
| GET | `/integrations/changelog/entries` | Stored changelog entries for the current BU, newest first. Reads only TDT's own DB — never hits GitHub. | viewer |
| POST | `/integrations/changelog/sync` | Pull merged PRs from the configured repo, upserting by PR number. 404 if no repo configured; 502 on a GitHub-side failure. | admin |
| POST | `/integrations/changelog/entries` | Add a manual changelog entry. Body: `{title, body?, url?, entry_date?}`. | admin |
| DELETE | `/integrations/changelog/entries/{entry_id}` | Delete one entry (manual or synced). | admin |

Synced entries have `source="github"` and `ref=<PR number>`; manual entries
have `source="manual"` and `ref=null`.

### Slack (bot token + channel)

| Method | Path | Description |
|---|---|---|
| GET | `/integrations/slack` | `{configured, token_tail, team_name, channel_id, channel_name}` |
| PUT | `/integrations/slack` | Body: `{token?, channel_id?, channel_name?}`. Verifies the token via Slack `auth.test` before persisting; 422 if no token is saved yet and none is supplied. |
| DELETE | `/integrations/slack` | Remove the bot token + channel. |
| POST | `/integrations/slack/test` | Re-verify the saved bot token. Returns `{ok, team, bot_user_id, url}`. |
| GET | `/integrations/slack/channels` | List the channels the bot can see (`conversations.list`). Each row has an `is_private` flag so the Settings UI can render a lock badge. |

The bot needs `chat:write`, plus `channels:read` to surface public
channels and `groups:read` to surface private ones. The bot must be
invited to the target channel before posts will land.

Notifications post on:

- **Auto-approve fired** (run was auto-approved on a 0/0/0 plan).
- **Run awaiting approval** (plan paused for human review).
- **Run failed** (plan / checkov / apply terminal failure).
- **Drift detected** (drift report transitioned a workspace to `drifted`).

---

## Webhooks — `/api/v1/webhooks` (public; HMAC-authenticated)

| Method | Path | Description |
|---|---|---|
| POST | `/webhooks/forgejo` | Forgejo push hook. HMAC-SHA256 via `X-Gitea-Signature`, validated against the legacy global `webhook.secret`. Maps branch → environment (`main`/`master`/`production`→`prod`, `staging`/`stage`→`staging`, else `dev`) to disambiguate same-named workspaces across environments, then triggers a plan run if `webhook_enabled` and the branch matches the workspace's tracked ref. |
| POST | `/webhooks/github` | Legacy global GitHub push hook (back-compat). HMAC-SHA256 via `X-Hub-Signature-256`. Matches every workspace whose `repo_url` references the pushed repo, filters by `webhook_enabled` + branch match + (if the payload lists changed files) whether the push touched that workspace's `tf_working_dir`, and triggers one plan run per remaining match. |
| POST | `/webhooks/github/{bu_slug}` | Per-BU GitHub push hook. Preferred over the legacy path — validates against `bu.<slug>.webhook.secret` (falling back to the legacy global secret for one release) and only matches workspaces belonging to that BU, so a repo-name collision across BUs can't cross-trigger. |

All three always return `202` with a JSON body describing what happened
(`{status: "accepted"|"ignored", ...}`) — a webhook sender never sees a 4xx
for "no matching workspace", only for a bad/missing signature (403) or
malformed JSON (400). Webhooks only ever create `plan` runs; applying still
requires a human (or auto-approve) approval.

---

## Internal — `/api/v1/internal` (state-token-auth)

Used by background services (drift-detector, liveness-detector) that run on
the same private network as the API and authenticate with the
`X-Terraducktel-State-Token` header instead of a JWT.

| Method | Path | Description |
|---|---|---|
| GET | `/internal/workspaces` | List every workspace, cross-BU. |
| POST | `/internal/drift/{workspace_id}/report` | Drift report submission — identical body/behavior to the user-facing `POST /drift/{workspace_id}/report`, including the Slack drift alert and Inventory refresh. |
| GET | `/internal/workspaces/{workspace_id}/aws-credentials` | Decrypted AWS creds for a workspace: `{access_key_id, secret_access_key, account_id, region}`. Honors the `state_aws_account_id` override, falling back to `aws_account_id`. Empty strings if the account has no stored credentials. |
| GET | `/internal/github-token` | Plaintext GitHub token for in-network crons that can't decrypt the config table themselves: `{token, source: "env"\|"config"\|"none"}`. |
| POST | `/internal/workspaces/{workspace_id}/auto-delete` | Cleanup hook used by the liveness detector when a workspace's repo path disappears upstream. Body: `{reason}`. Deletes the workspace + its runs/drift reports/state locks and audits as `auto_delete_orphan`. Idempotent (204) on an already-missing workspace. |

---

## Runtime config — `/api/v1/runtime-config`

Admin-tunable worker timings and poll intervals (JWT auth = a superadmin
concept in practice, since it's admin+ everywhere and cross-cutting rather
than BU-scoped).

| Method | Path | Description | Min role |
|---|---|---|---|
| GET | `/runtime-config` | Every tunable with its `{value, default}`. | admin |
| PUT | `/runtime-config/{key}` | Update one tunable. Body: `{"value": <number>}`. 404 for an unknown key, 422 if the value fails validation (e.g. not `> 0`). | admin |

Workers pick up a new value within ~60s (the same `ConfigService` TTL cache
used everywhere else) without a container restart. Known tunables include
`repo_sync.poll_seconds`, `auth.access_token_expire_minutes`,
`auth.refresh_token_expire_hours` — see `app/services/runtime_settings.py`
for the authoritative catalog and defaults.

---

## Terraform state — `/api/v1/state/{workspace_id}` (state-token-auth)

Implements Terraform's HTTP state backend protocol. Not meant to be called
directly — Terraform itself talks to this via the executor's generated
backend config.

| Method | Path | Description |
|---|---|---|
| GET | `/state/{workspace_id}` | Fetch the tfstate body for a workspace. **404** (not 200 + empty JSON) when no state has ever been written — matches the HTTP backend spec's "create on first write" semantics and avoids Terraform misreading an empty body as a corrupted state file. **503** on a real S3/connectivity failure (never silently treated as "no state", which would let `apply` recreate every existing resource). |
| POST | `/state/{workspace_id}` | Upload an updated tfstate body. Runs a secret scanner over the JSON first; a suspected leaked secret is rejected with **422**. |
| POST | `/state/{workspace_id}/lock` | Acquire a Postgres advisory lock for this workspace (TF HTTP backend lock protocol). **409** with Terraform-shaped lock-info JSON if already held. |
| DELETE | `/state/{workspace_id}/lock` | Release the advisory lock. Idempotent — releasing an already-unlocked workspace is **200**, not an error; only a genuine holder-id mismatch is **409**. |

State lives in S3 (LocalStack in dev) at
`{tf_working_dir}/terraform.tfstate` inside the bucket owned by the
workspace's (or its `state_aws_account_id` override's) AWS account. Locking
is `pg_try_advisory_lock`-based, not DynamoDB.

---

## Status codes used everywhere

- `200 / 201 / 204` — success.
- `202` — accepted for async processing (webhooks, drift scan trigger).
- `400` — bad input (e.g. invalid Slack token, channel id missing, BU scoped to "all" where a concrete BU is required).
- `401` — JWT or API key missing/expired/revoked.
- `403` — role or API-key capability insufficient, or an API key stepping outside its workspace allowlist, or an identity endpoint rejecting an API key outright.
- `404` — resource not found (including cross-BU access, which 404s rather than 403s to avoid leaking another tenant's resource existence).
- `409` — state-machine conflict (e.g. approving/cancelling a run in the wrong state, deleting a git-synced workspace, duplicate account/subscription/workspace identity tuple, regenerating a revoked/expired API key).
- `422` — schema validation failed (including business-rule validation like an out-of-BU account reference).
- `502` — upstream integration unreachable (GitHub, Slack, Infracost).
- `503` — state backend (S3) unavailable.

## Audit actions you'll see

`login`, `workspace.create`, `workspace.update`, `workspace.delete`,
`workspace.force_delete`, `workspace.force_unlock`, `auto_delete_orphan`,
`run.trigger`, `approve`, `auto_approve`, `auto_apply_skipped`, `reject`,
`aws_account.create`, `aws_account.update`, `integration.github.set`,
`integration.slack.set`, `api_key.create`, `api_key.regenerate`,
`api_key.revoke`, …
