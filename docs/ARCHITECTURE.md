# Architecture

This is the map of Terraducktel (TDT): what runs, what it stores, and how a
change moves from a Git push to a live cloud resource. It is meant to be
read start-to-finish by a new contributor, or grepped section-by-section by
an AI coding agent that needs to know where a concept lives before touching
it.

For the wire-level endpoint catalog, see [`docs/API.md`](API.md). This
document is the single authoritative reference for everything else.

---

## 1. What Terraducktel is

Terraducktel is a **self-hosted Terraform (and Helm) orchestration
platform**. It exists as a zero-license-cost, run-it-yourself alternative to
a paid Terraform-orchestration SaaS: it imports Terraform modules (and Helm
charts) from a Git repository, runs each change through a gated
`plan → policy scan → cost estimate → human approval → apply` pipeline
against real AWS accounts, Azure subscriptions, or Kubernetes clusters, and
continuously checks the live cloud for drift from what's codified. Every
tenant ("Business Unit") gets its own cloud accounts, Git integration, and
workspace tree, all running on a single `docker compose` stack with no SaaS
dependency and no per-seat billing.

The core design bet is a **thin custom orchestrator** rather than adopting
an existing heavyweight CI/CD platform: a FastAPI backend owns all
state/approval logic, a purpose-built executor container does the actual
`terraform`/`helm` work, and Postgres — not a hosted state-locking service —
is the single source of truth for both metadata and locking.

---

## 2. Service topology

```
                              ┌─ traefik (80 / 443 / dashboard :18080) ─┐
                              │        (reverse proxy + auto TLS)       │
                              └──────────────────┬───────────────────────┘
                                                  │
 browser ──────────────────► ui (nginx, :3001) ───┤
                                                  │
                                                  ▼
                                         api (FastAPI, :8001)
                                                  │
                    ┌─────────────────────────────┼──────────────────────────────┐
                    ▼                              ▼                              ▼
             postgres (:5432,               localstack S3 (:4566,          docker socket
             internal only)                  dev tfstate backend;          (launches executor
             metadata, config,                AWS S3 in prod)               containers)
             audit, state locks
                    ▲                                                             │
                    │                                                             ▼
             drift-detector  ──── reads state + AWS creds via api/internal ── executor / executor-helm
             liveness-detector ── health pings → Slack                       (terraform plan/apply OR
             pg-backup ────────── periodic pg_dump → volume                   helm diff/upgrade)
                                                                                    │
                                                                    S3 (tfstate) + Postgres (locks)
                                                                    + target AWS account / Azure sub /
                                                                      Kubernetes cluster

 forgejo (:3002, self-hosted Git + Actions-compatible CI) ── act_runner (executes Forgejo workflows)
```

Everything above runs from one `docker-compose.yml`. In production the same
API/UI/executor images can instead run on AWS ECS (see
[§11 Deployment topology](#11-deployment-topology)); the architecture is
identical, only the container launcher and the state bucket change.

### Docker Compose services

| Service | Image / build | Purpose |
|---|---|---|
| `postgres` | `postgres:16-alpine` | App metadata, encrypted config, audit log, Terraform state locks. |
| `localstack` | `localstack/localstack:3` | S3-compatible Terraform state backend for local dev (no AWS account needed). |
| `forgejo` | `codeberg.org/forgejo/forgejo:7` | Self-hosted Git hosting + GitHub Actions-compatible CI, for repos that don't live on github.com. |
| `act_runner` | `gitea/act_runner:0.2.11` | Executes Forgejo Actions workflows. |
| `api` | build `services/api` | FastAPI orchestrator: auth, RBAC, workspaces, runs, approvals, drift, policies, audit. The only service that talks to Postgres directly. |
| `ui` | build `services/ui` (served by nginx) | React + Vite management UI. |
| `traefik` | `traefik:v3.0` | Reverse proxy + automatic TLS (Let's Encrypt) in prod-like deployments. |
| `drift-detector` | build `services/drift-detector` | Periodic loop: reconciles live cloud resources against Terraform state via the API (does not run `terraform` itself). |
| `liveness-detector` | build `services/liveness-detector` | Simple health watchdog; posts to Slack on prolonged downtime. |
| `pg-backup` | build `services/pg-backup` | Periodic `pg_dump` of Postgres to a volume. |
| `executor` | build `services/executor` (`Dockerfile`) | On-demand: one container launched per Terraform run (`kind=terraform`). Bundles Terraform CLI + Checkov + conftest. |
| `executor-helm` | build `services/executor` (`Dockerfile.helm`) | On-demand: launched per Helm run (`kind=helm`). Adds `helm`, `kubectl`, `helm-diff`, `kubeconform`, AWS CLI. Build with `docker compose --profile executor build executor-helm`. |

See `docker-compose.yml` for ports and the `Makefile` for day-to-day commands.

---

## 3. Data model

All models live under `services/api/app/models/`, one file (mostly) per
table, SQLAlchemy 2.0 declarative, async everywhere. Alembic migrations are
forward-only (`services/api/alembic/versions/NNN_*.py`).

### Identity & tenancy

| Model | Table | What |
|---|---|---|
| `User` | `users` | Email + bcrypt hash, legacy global `role` (admin/operator/viewer, kept as a fallback), `is_superadmin` (cross-BU bypass flag), `auth_provider` (local/oidc), `external_id`, `display_name`. |
| `BusinessUnit` | `business_units` | A tenant container: `slug` (immutable, used in config keys/URLs) + `name`. Every workspace and cloud account belongs to exactly one BU. |
| `UserBusinessUnit` | `user_business_units` | Membership: `(user_id, business_unit_id, role)` where `role` is `operator` or `viewer`. Superadmins don't need rows here — they bypass BU scoping entirely. |
| `UserPresence` | `user_presence` | Ephemeral per-user "last seen" ping + selected BU slug, upserted every 30s by the UI for the top-bar avatar stack. |

### Cloud targets

| Model | Table | What |
|---|---|---|
| `AwsAccount` | `aws_accounts` | One row per AWS account onboarded to TDT: 12-digit `account_id`, a dedicated `state_bucket`, and Fernet-encrypted access key/secret. Unique per `(business_unit_id, account_id)`. |
| `AzureSubscription` | `azure_subscriptions` | Mirrors `AwsAccount` for Azure: `subscription_id`, `tenant_id`, `client_id` + encrypted service-principal secret. Terraform state for Azure workspaces still lives in the S3 backend (via a linked AWS account) — there's no parallel Azure Storage state backend yet. |
| `K8sCluster` | `k8s_clusters` | One row per Kubernetes cluster for Helm workspaces: `name`, optional `server_url`, `default_namespace`, encrypted `kubeconfig`, optional `aws_account_id` (for EKS clusters whose kubeconfig auths via `aws eks get-token`). |

### Workspaces & runs

| Model | Table | What |
|---|---|---|
| `Workspace` | `workspaces` | One Terraform leaf module or one Helm chart. Canonical identity is `(business_unit_id, aws_account_id, region, environment, tf_working_dir)`. Carries `repo_url`/`repo_ref` (Git source), `kind` (`terraform` default or `helm`), `cluster_id` (helm target), `azure_subscription_id` (optional Azure target), `drift_status`, `path_status` (`ok`/`orphaned`/`unknown` — tracks whether the leaf still exists at `repo_ref`), `webhook_enabled`. |
| `Run` | `runs` | One plan/apply/destroy execution. FSM `status` (see [§4](#4-the-run-fsm)), captured `branch`, `plan_output`/`plan_json`, base64 `tfplan_b64` (the exact binary re-applied post-approval), encrypted `variables_encrypted` (per-run TF_VAR overrides), `policy_status`, `auto_approve_if_no_changes`/`auto_approve_skip_apply`. |
| `RunStep` | `run_steps` | Per-step timeline row (Git Clone → Checkov → Plan → OPA → Cost → Awaiting Approval → Apply → …), kind-aware (Terraform vs. Helm step lists live in `run_step.py`). |
| `RunArtifact` | `run_artifacts` | Blob output attached to a run (plan output, logs, checkov report). |
| `RunJob` | `run_jobs` | The worker's queue: one row per executor launch attempt, states `queued → picked → done|failed`, with heartbeats so a reaper can detect and recover a dead launch. Decouples "a run was triggered" from "an executor container is actually running." |
| `StateLockEntry` | `state_lock_entry` | Backing row for the Postgres advisory lock held while a workspace's Terraform state is checked out (see [§7](#7-state-backend)). |

### Policy, drift & inventory

| Model | Table | What |
|---|---|---|
| `Policy` / `PolicyVersion` | `policies` / (version history table) | BU-scoped OPA/conftest rego rule authored in the UI, with append-only version snapshots on every edit and a `severity` (`block`/`warn`/`info`). |
| `DriftReport` | `drift_reports` | Latest drift snapshot per workspace: `has_drift` + a 4-way breakdown (`modified_count`, `untracked_count`, `deleted_count`, `mismatch_count`) + a `resources[]` JSON drill-down. |
| `CloudAsset` | `cloud_assets` | One row per discovered cloud resource, classified into an IaC state (`codified`, `drifted`, `ghost`, `unmanaged`, `service_managed`, `ignored`, `undetermined`). Feeds the Inventory dashboard's codification percentage. |
| `InventoryIgnoreRule` | `inventory_ignore_rules` | Per-BU glob/type rules that reclassify noisy live resources to `ignored` so they don't count against codification coverage. |

### Config, variables & audit

| Model | Table | What |
|---|---|---|
| `Config` / `ConfigHistory` | `config` / `config_history` | Generic key/value runtime config (see [§6](#6-encryption-model)); every write is versioned in `config_history`. |
| `GlobalVariable` / `WorkspaceVariable` | `global_variables` / `workspace_variables` | Encrypted `TF_VAR_*` sources at BU-global and per-workspace scope. Merge order at executor launch: `global ← workspace ← run` (run-scope values live inline on `Run.variables_encrypted`, not here). |
| `AuditLog` | `audit_logs` | Append-only, hash-chained audit trail (see [§10](#10-webhooks-notifications-audit-log)). |
| `APIKey` | `api_keys` | Scoped automation credential (see [§5](#5-auth--rbac)). |
| `ChangelogEntry` | `changelog_entries` | TDT-owned changelog shown in Settings → Changelog; rows are `github` (synced from merged PRs) or `manual` (admin-authored). |

---

## 4. The Run FSM

A `Run` represents one `plan`, `apply`, or `destroy` execution against a
workspace. `Run.status` moves through a strict state machine
(`app/models/run.py: RunStatus` + `_VALID_TRANSITIONS`); invalid transitions
raise, so the executor and API can never desync the visible status from
what actually happened.

```
PENDING ──► RUNNING ──┬─► PLANNING ──┬─► PLANNED ──► AWAITING_APPROVAL ──► APPLYING ──► APPLIED
                       │              └─► AWAITING_APPROVAL ────────────────┘
                       └─► PLANNED ────────┘
                       └─► AWAITING_APPROVAL  (unified plan→apply: plan runs inline, no PLANNED hop)

any non-terminal state ──► FAILED     (executor can report a fatal error from anywhere)
PENDING / RUNNING / PLANNING / PLANNED / AWAITING_APPROVAL ──► CANCELLED
```

- **PENDING → RUNNING**: `POST /api/v1/runs` creates the row and enqueues a
  `RunJob`; the worker picks it up and launches an executor container.
- **RUNNING → PLANNING/PLANNED → AWAITING_APPROVAL**: the executor clones
  the repo, runs Checkov, `terraform init`/`plan` (or `helm diff` for Helm
  workspaces), runs the OPA policy gate, runs cost estimation, and PATCHes
  the plan JSON + `tfplan` binary back to the API. The run then pauses.
- **AWAITING_APPROVAL → APPLYING → APPLIED**: a second executor container is
  launched against the **exact same `tfplan` binary** captured at plan time
  — no drift can sneak in between review and apply.
- **APPLIED / FAILED / CANCELLED** are terminal — no further transitions.

`APPLYING` from `AWAITING_APPROVAL` is a manual approval, an
auto-approval, or a rejection:

- **Manual approve/reject**: `POST /api/v1/approvals/{run_id}/approve` or
  `/reject`, handled by `app/services/approval_service.py`.
- **Auto-approve**: if the run was created with
  `auto_approve_if_no_changes=True` and the plan comes back with a clean
  0-add/0-change/0-destroy summary and all gates green, `system_auto_approve`
  stamps an audit entry attributed to `user_id=None` ("system") and either
  proceeds to a real apply or (if `auto_approve_skip_apply=True`)
  short-circuits straight to `APPLIED` with no executor spawned.

### 4-eyes approval — **revoked**

An earlier design required the approver to differ from the triggering user
("4-eyes"). **This rule has been removed.** Any user with `operator` role or
higher may approve or reject **any** run in their Business Unit, including
one they triggered themselves. The `runs.reviewer_id` column and the
`GET /users/eligible-reviewers` endpoint are kept only for backward
compatibility with pre-removal rows/clients and do no enforcement.

> Do not reintroduce a 4-eyes check without explicit approval — see
> `CLAUDE.md` at the repo root.

### Who can trigger / approve

| Action | Minimum role |
|---|---|
| Trigger plan / apply / destroy | `operator` |
| Approve / reject a run | `operator` (any operator+, including the triggerer) |
| Cancel a run | `operator` |
| Read runs / drift / audit | `viewer` |

Full lifecycle detail (executor env wiring, step lists, Helm mapping,
cancellation, logs) lives in `services/api/app/services/executor_service.py`
and `services/executor/entrypoint.sh`.

---

## 5. Auth & RBAC

### JWT (interactive sessions)

- HS256, secret from the `config` table (`JWT_SECRET`, bootstrap value from
  an env var read once at first boot).
- **Access token**: default 480min/8h (`auth.access_token_expire_minutes`,
  admin-tunable via Runtime Config), claims `sub`, `email`, `role`,
  `is_superadmin`, `name`, `type: "access"`. `is_superadmin` is a top-level
  claim so the UI can render superadmin affordances without an extra `/me`
  round trip.
- **Refresh token**: 24h, claims `sub`, `type: "refresh"`.
- Issued by `POST /api/v1/auth/token` (local email+password), refreshed via
  the refresh endpoint. Password hashing is bcrypt; no plaintext at rest.
- Code: `services/api/app/auth/jwt.py`.

### OIDC / SSO

A pluggable auth-mode switch (`local` / `oidc` / `both`), config read from
env vars first with a `config`-table fallback (`app/auth/oidc.py`). Local
docker-compose dev has none of the `AUTH_OIDC_*` vars set and stays on
`local` with zero configuration. In an OIDC deployment, groups in the
identity token map to TDT roles (including a `superadmin` target) via a
configurable JSON mapping, re-evaluated on every sign-in. Tested against a
generic OIDC IdP — see `services/api/app/auth/oidc.py` for the group-mapping
implementation.

### Role hierarchy

`viewer(0) < operator(1) < admin(2)`, defined in
`services/api/app/auth/rbac.py`. `require_role(Role.x)` is a FastAPI
dependency enforcing a **minimum** role; a higher role can do everything a
lower one can.

| Capability | Min role |
|---|---|
| Read workspaces / runs / drift / audit | `viewer` |
| Trigger plan/apply/destroy, approve/reject a run | `operator` |
| Configure integrations (Git PAT, Slack, Checkov mode…), toggle per-workspace webhook | `operator` |
| User CRUD, AWS/Azure account CRUD, cluster CRUD, workspace delete, API key management, policy CRUD | `admin` |

### Superadmin

`users.is_superadmin` is a **cross-BU flag**, not a fourth role value on
`users.role`. A superadmin sees every Business Unit, every workspace, every
cloud account, bypassing the per-BU membership filter entirely. It's
intended for break-glass/ops use. See `app/auth/bu_context.py` for the
`X-Business-Unit` header resolution logic and `PATCH /users/{id}` in
`docs/API.md` for the promotion/demotion contract.

### Business Units (multi-tenancy)

Every `workspace`, `aws_account`, `azure_subscription`, and `k8s_cluster`
belongs to exactly one BU via a NOT NULL `business_unit_id`. The UI sends
`X-Business-Unit: <slug>` on every request; the backend resolves scope via
`app/auth/bu_context.py::current_bu` (superadmin + no header → all BUs;
member + no header → their first BU; `all` → all BUs for superadmins only,
403 for members; `<slug>` → that BU if the caller is a member/superadmin).
Non-superadmins join one or more BUs through `user_business_units` with a
per-BU role (`operator` | `viewer`); the legacy `users.role` column is kept
for one release as a fallback for code paths that haven't migrated to
per-BU resolution.

### API keys (automation)

Long-lived bearer credentials for non-interactive use, additive to the JWT
path. Token format `tdt_<urlsafe>`, shown once at creation, stored only as a
SHA-256 hash (`api_keys.token_hash`) plus a display prefix.

Each key is pinned to **one Business Unit** (forced regardless of any
`X-Business-Unit` header the caller sends), optionally restricted to a
**workspace allowlist**, and capped by a **capability tier**:

| Capability | Equivalent to | Notes |
|---|---|---|
| `read` | `viewer` | Read-only. |
| `plan` | `operator` (plan-only) | Can trigger plans, not applies. |
| `apply` | `operator` (full) | Can trigger + approve/apply. |
| `admin` | `admin`, minus identity | Everything `apply` can do **plus** workspace create/discover/import/update/delete, AWS/Azure accounts, clusters, policies, drift, integrations, variables — the full admin surface **except** identity management. |

The effective role for a key request is `min(owner's real role, capability
ceiling)` — a key can never exceed what its owner could already do.
**Identity endpoints are interactive-only, even for `admin`-tier keys**: the
API-keys and users routers, plus the Business-Unit superadmin gate, carry a
blanket `forbid_api_keys` dependency, so no key — however powerful — can
mint/revoke keys, manage users, or create/update BUs. Those surfaces gate on
the *owner's* `is_superadmin`, which an `admin` key would otherwise inherit
as a privilege escalation if this guard didn't exist.

Management: `POST/GET/DELETE /api/v1/api-keys`, `admin`-only, every
create/revoke written to `AuditLog`. UI: Settings → **API keys** tab
(admin-only). Full detail in `docs/API.md` and `app/auth/rbac.py`.

---

## 6. Encryption model

**Invariant:** exactly one secret, `CREDENTIAL_ENCRYPTION_KEY`, seeds every
encrypted value in the system. If it's lost, every encrypted row
(credentials, kubeconfigs, secret config values, secret variables) becomes
permanently unreadable — there is no recovery path other than re-entering
the plaintext. If it's ever rotated, everything encrypted under the old key
must be re-encrypted first. There is no fallback/default key in code —
`app/auth/encryption_key.py::get_credential_encryption_key()` raises at
import time if the env var is unset, by design, so a misconfigured
deployment fails loudly instead of silently using a predictable key.

**Derivation:** each domain (AWS credentials, Azure credentials, Kubernetes
kubeconfigs, generic `config` secrets, workspace/global variables) derives
its **own** Fernet key from the same root key via HKDF-SHA256 with a
**distinct, hardcoded salt** per domain (e.g.
`b"terraducktel-aws-credentials-v1"`, `b"terraducktel-config-v1"`,
`b"terraducktel-azure-credentials-v1"`, `b"terraducktel-variables-v1"`).
This means a ciphertext leaked from one domain can't be replayed or
confused with another, and each domain can rotate its salt independently in
a future migration without touching the root key. The pattern is identical
everywhere it appears — `aws_account_service.py`, `azure_subscription_service.py`,
`cluster_service.py`, `config_service.py`, `variable_service.py` — new
encrypted domains should copy it rather than invent a new scheme.

**What's encrypted:**
- AWS access key / secret access key (`aws_accounts`).
- Azure service-principal secret (`azure_subscriptions`).
- Kubernetes kubeconfig (`k8s_clusters`).
- Any `config` row with `is_secret=True` (GitHub PAT, Slack bot token,
  Infracost API key, webhook secrets, `JWT_SECRET` itself once persisted).
- Global and per-workspace Terraform variables marked secret
  (`global_variables` / `workspace_variables`).
- Per-run variable overrides (`runs.variables_encrypted`) — encrypted as one
  JSON blob so the apply phase can replay exactly what the plan phase saw.

**What's never returned in plaintext once saved:** every GET for a secret
resource returns `{configured: bool, masked_tail: "...ab12"}`, never the
value. This is enforced at the service layer, not just the router, so a
future endpoint can't accidentally leak it.

---

## 7. State backend

Terraform state is served by a custom **Terraform HTTP state backend**
implemented in the API itself (`app/routers/state.py` +
`app/services/state_service.py` + `app/services/s3_state_service.py`), not
by pointing `terraform init` directly at S3. The executor's backend config
points at `{API_URL}/api/v1/state/{workspace_id}`, authenticated with the
shared `TERRADUCKTEL_STATE_TOKEN` secret.

- **Persistence**: the actual bytes live in S3 — LocalStack in dev, real AWS
  S3 in production. **Per-account bucket isolation**: each onboarded
  `AwsAccount` row owns its own dedicated `state_bucket`, so one account's
  Terraform state is never physically co-located with another's, even
  within the same BU. Non-AWS workspaces (Helm, or Azure workspaces without
  their own state backend) still resolve to an AWS account's bucket via
  `workspace.state_aws_account_id` — a workspace can be "grouped" outside
  the AWS-account tree in the UI while its tfstate still lives in an S3
  bucket owned by some account.
- **Key scheme**: `{tf_working_dir}/terraform.tfstate` — the S3 key mirrors
  the workspace's git path exactly (`app/routers/state.py::_service_for`).
  Isolation between workspaces comes from the per-account dedicated bucket,
  not a key prefix, so two same-named leaves under different account
  directories never collide. (`Workspace.state_path`/`state_key`, which build
  a `tfstate/{account}/{region}/{env}/...` key, are legacy and unused by the
  live state backend.)
- **Locking**: `pg_try_advisory_lock` on a numeric key derived from the
  workspace, backed by the `state_lock_entry` table for bookkeeping —
  **deliberately not DynamoDB**. This keeps the whole state story inside
  Postgres, which the platform already depends on, with no second AWS
  service to provision, pay for, or lose availability to. Terraform ≥1.10
  additionally supports native S3 `use_lockfile = true`, but TDT's own
  advisory-lock scheme is what actually gates concurrent runs against the
  same workspace at the API layer, independent of what the backend config
  says.
- Helm workspaces have **no external state backend at all** — Helm release
  state lives in-cluster, so the executor skips the AWS-state-token and
  S3/HTTP backend wiring entirely for `kind=helm` runs.

---

## 8. Executor lifecycle

The executor is a short-lived container launched **once per phase** (plan,
then — after approval — apply) by `app/services/executor_service.py`. Two
image variants share one `entrypoint.sh`, branching on a `WORKSPACE_KIND`
env var:

- `services/executor/Dockerfile` — Terraform CLI (bundles Terraform 1.10,
  the floor version because it's the first release supporting
  `use_lockfile = true`), Checkov, conftest.
- `services/executor/Dockerfile.helm` — adds `helm`, `kubectl`,
  `helm-diff`, `kubeconform`, the AWS CLI (for EKS `aws eks get-token`
  auth), reusing the same entrypoint.

### Terraform workspaces (`kind=terraform`)

1. Clone the repo at the workspace's `repo_ref` (shallow).
2. Load any per-workspace `terraducktel.yaml` override (Terraform version
   pin, Helm chart config for helm workspaces).
3. Load merged variables (`global ← workspace ← run`), decrypted once.
4. **Checkov** scan against the source HCL — a hard gate, configurable via
   Settings → Checkov mode. Runs *before* `terraform init`.
5. `terraform init` against the HTTP state backend, then
   `terraform plan -out tfplan.bin` + `terraform show -json tfplan.bin`.
6. **OPA policy check** (`conftest`) against the *plan JSON* — this runs
   **after** plan, unlike Checkov, because it needs the resolved plan, not
   just source HCL. Policy sources merge three ways at run time: bundled
   defaults baked into the image (from the repo's `policies/*.rego`), the
   BU's DB-authored `Policy` rows, and an optional external git policy
   repo. `conftest` runs once per policy so each finding carries its own
   severity. The per-BU `opa.mode` config (`off` default / `warn` /
   `enforce`) is the master switch — only `enforce` + a `block`-severity
   violation actually fails the run, and it fails **before** the approval
   step is reached.
7. **Cost estimation** via Infracost, if a key is configured — best-effort,
   never fails the run.
8. PATCH the plan JSON + `tfplan` blob back to the API →
   `status = "awaiting_approval"`. The container exits; the plan binary is
   now sitting in `runs.tfplan_b64`.
9. On approval, a **second** executor container launches with the apply
   phase, reads back the same `tfplan.bin`, runs `terraform apply
   tfplan.bin`, and PATCHes the final status.

### Helm workspaces (`kind=helm`)

Same gated pipeline, same approval boundary, same run timeline — only the
command vocabulary changes:

| TDT command | Terraform | Helm |
|---|---|---|
| `plan` | `terraform plan` | `helm diff upgrade` |
| `apply` | `terraform apply tfplan.bin` | `helm upgrade --install` |
| `destroy` | `terraform destroy` | `helm uninstall` |
| lint | (Checkov) | `helm lint` + `kubeconform` |

The launcher injects `WORKSPACE_KIND=helm`, selects the `executor-helm`
image, writes the target cluster's decrypted kubeconfig into the container,
and skips all AWS-state-token / S3 backend wiring — there's no `terraform
init` step and no state lock to take. Chart config comes from a
`terraducktel.yaml` `helm:` block (`release_name`, `namespace`, `chart`,
`repo`, `values[]`). There is no OPA step for Helm in this version (Checkov
and OPA both currently target Terraform plan JSON / HCL only).

Full detail, env var list, and the Helm cluster/EKS credential story live in
`services/api/app/services/executor_service.py` and
`services/api/app/services/cluster_service.py`.

---

## 9. Drift detection

`services/drift-detector/` is a long-running Python loop (also functioning
as the cloud asset inventory collector), on its own container, polling
every `DRIFT_INTERVAL_SEC` (default 300s locally, 1800s in prod). It
**does not clone repos or run `terraform`** — it reuses state the API
already holds:

1. Fetch every workspace via `GET /api/v1/internal/workspaces`
   (state-token authenticated).
2. For each workspace, fetch its Terraform state from the HTTP state
   backend and classify every `mode == "managed"` resource as **codified**.
3. Fetch the workspace's AWS credentials via
   `GET /api/v1/internal/workspaces/{id}/aws-credentials` and enumerate
   live resources via the AWS Resource Groups Tagging API. Any live ARN
   absent from every workspace's state → **unmanaged**.
4. POST the classified asset set back to
   `.../internal/drift/{id}/report`; the API upserts `cloud_assets`.

This gives cheap, frequent coverage of "what exists but isn't codified"
without the cost of a real `terraform plan` per cycle. **Attribute-level**
drift (a resource that's both codified *and* changed) and state↔config
`mismatch` still require an actual plan and are a separate, plan-based
concern — they don't come from this collector.

`workspaces.drift_status` is `clean` / `drifted` / `unknown` (not yet
scanned, scan errored, or no `repo_url`). Surfaced on the Dashboard, the
per-workspace drift badge, and a dedicated Drift page with a 4-category
breakdown. Full detail lives in `services/drift-detector/detector.py`.

`liveness-detector` is a separate, simpler container that just pings core
services and Slack-alerts on prolonged downtime — not part of the drift
pipeline.

---

## 10. Webhooks, notifications, audit log

### Webhooks

Per-workspace opt-in (`workspace.webhook_enabled`, default `false`). When
enabled, a push to the workspace's `repo_ref` on GitHub or Forgejo triggers
a Run automatically. Both providers land at HMAC-verified
`POST /api/v1/webhooks/...` endpoints. A per-BU webhook path
(`/webhooks/github/{bu_slug}`) validates against a BU-namespaced secret and
only matches workspaces in that BU; a legacy unscoped path
(`/webhooks/github`) is kept for backward compatibility and matches across
all BUs by repo-URL substring.

### Notifications

`app/services/notification_service.py` fires on run and drift events:

- **Slack** — two mechanisms coexist: a simple incoming-webhook path
  (`slack.webhook_url` in config) for plan/approval/drift alerts, and a
  richer Slack **bot** path (per-BU bot token + channel, `slack.py`) used
  for the run-lifecycle messages (auto-approved, awaiting approval, failed,
  drift detected) — these show the workspace's display name + leaf path,
  not just its environment tag, and include deep links back into the UI.
- **SMTP** — best-effort email via `smtplib`, configured through
  `smtp.host`/`smtp.port`/`smtp.from`/`smtp.to` (+ optional
  `smtp.username`/`smtp.password`) in the `config` table. Silently skipped
  if `smtp.host` isn't set.

All notification sends are best-effort — a Slack or SMTP failure never
fails the underlying run.

### Audit log + hash chain

Every privileged action (login, run trigger/approve/reject, workspace
create/delete, integration config writes, account create/delete, API key
create/revoke, policy edits) writes an `AuditLog` row. The log is
**append-only and tamper-evident**: each row carries `prev_hash` and
`entry_hash`, where

```
entry_hash = sha256(prev_hash || canonical_row_json)
```

computed in Python *before* insert, and a database trigger re-derives and
checks it on every `INSERT`, rejecting the write if the Python-computed
hash doesn't match — and rejecting `UPDATE`/`DELETE` outright at the row
level. This means a chain break (a row that doesn't reproduce its stored
hash from its predecessor) is detectable by walking the table, which is
exactly what the audit-log verifier endpoint does. `resource_type`,
`resource_id`, `action`, and an optional `details` JSON blob describe what
happened; `user_id=None` marks system-initiated entries (e.g.
auto-approval).

---

## 11. Deployment topology

### Local dev (docker compose)

`make up` brings up every service in [§2](#2-service-topology). No AWS
account is required — LocalStack stands in for S3, Forgejo stands in for
GitHub, and `local` auth mode needs zero configuration. `make seed-db`
inserts three dev users (`admin@test.com` / `operator@test.com` /
`viewer@test.com`, all password `password123`). See the `Makefile` for the
full command list.

### AWS ECS production path (optional)

TDT can run the same five core images (`api`, `ui`, `drift-detector`,
`liveness-detector`, `executor` — plus `executor-helm` if Helm workspaces
are in use) on **AWS ECS**, fronted by an ALB, with RDS Postgres and real
S3 replacing the compose `postgres`/`localstack` services. This path is
driven by two things that are kept in lockstep on purpose:

- **`.github/workflows/deploy-dev.yml`** — runs on every push to `dev`
  (i.e., every merged PR once `dev` is branch-protected). Builds all 5
  service images in a matrix, pushes them to ECR tagged
  `v0.1.0-<short-sha>`, checks out a **separate, private**
  `terraform-infra` repo containing the actual ECS/RDS/ALB stack
  definitions (not part of this repo — infrastructure-as-code for the
  platform's *own* hosting is deliberately decoupled from the app repo),
  and runs `terraform apply` to roll the ECS services onto the new tag.
- **`scripts/deploy-to-aws.sh`** — the same build-and-push logic, runnable
  locally for ad-hoc deploys. It intentionally does **not** run
  `terraform apply` itself; a human runs that from the infra repo
  afterward. The two paths are documented as required to stay in sync.

**CI auth is OIDC — no long-lived AWS keys stored in GitHub.** The workflow
assumes an IAM role (`github-actions-terraducktel-deploy`) via
`aws-actions/configure-aws-credentials`, using GitHub's OIDC token
(`https://token.actions.githubusercontent.com`, audience `sts.amazonaws.com`).
The role's trust policy restricts `sub` to `repo:<your-org>/terraducktel:ref:refs/heads/dev`,
so only workflows running on `dev` in this specific repo can assume it. Six
Terraform stack inputs with no defaults (domain name, ACM cert ARN, DB
password, credential encryption key, JWT secret, state token) are injected
as `TF_VAR_*` from GitHub Actions secrets — set once, treated as immutable
after first apply, since rotating any of them invalidates live sessions or
makes existing encrypted data unreadable.

Example account id used throughout these docs and scripts: `111111111111`
(a placeholder — substitute your own 12-digit AWS account id).

---

## 12. Extension points

Where to look when adding something new:

| Adding... | Start here |
|---|---|
| A new API endpoint / resource type | `app/schemas/<domain>.py` → `app/services/<domain>_service.py` → `app/routers/<domain>.py` (thin, `require_role`-gated) → `tests/test_<domain>.py` → update `docs/API.md`. |
| A new encrypted credential type | Copy the Fernet/HKDF pattern in `aws_account_service.py` / `cluster_service.py` — **derive a new domain-specific HKDF salt**, don't reuse an existing one or roll a new encryption scheme. See [§6](#6-encryption-model). |
| A new cloud provider target (beyond AWS/Azure/K8s) | Model it like `AzureSubscription`/`K8sCluster`: its own table, its own encrypted-credential service, a `workspace.<provider>_id` FK, and executor_service branching analogous to the `kind=helm` path in `executor_service.py`. |
| A new run step (scanner, gate, reporter) | `run_step.py`'s `DEFAULT_STEP_NAMES`/`HELM_STEP_NAMES` lists (order matters — it's display order) + the matching block in `services/executor/entrypoint.sh`, wired through `executor_service.py`'s env dict if it needs config. |
| A new policy engine or gate | Follow the OPA/conftest pattern in `policy_service.py` + the `run_opa_policy_check` block in `entrypoint.sh` — a gate should be config-switchable per BU (`off`/`warn`/`enforce`-style), never hard-blocking by default. |
| A new notification channel | `notification_service.py` — keep sends best-effort (never fail the run) and read config through `ConfigService`, not a new env var. |
| A new DB table/column | New Alembic revision (`NNN_<slug>.py`), never edit a merged one. |
| A new UI page/component | Follow the existing token/Button/primitive conventions in `services/ui/src/components/ui.tsx` — no new inline hex colors, no new Tailwind color names. |
| Anything touching Business Unit scoping | Always thread `business_unit_id` through creates and filter reads via `current_bu`. The FK is NOT NULL; there is no "unscoped" row. |

Cross-reference: [`docs/API.md`](API.md) for the full endpoint catalog.
