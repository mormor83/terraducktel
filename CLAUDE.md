# CLAUDE.md — Terraducktel (TDT)

Index for Claude. Treat `docs/ARCHITECTURE.md` + `docs/API.md` as the
authoritative reference material — read the relevant section there before
touching that area.

## What is Terraducktel
Self-hosted Terraform orchestration platform. Imports Terraform modules from a
Git repo, runs `plan → checkov → cost-estimate → human approval → apply` against
AWS workspaces, tracks drift. Also runs **Helm charts** through the same gated
pipeline (`workspace.kind=helm` → `helm diff → approval → helm upgrade` against a
Kubernetes cluster; see the Executor and Workspaces sections of
`docs/ARCHITECTURE.md`). Multi-tenant via Business Units — each BU owns its own
AWS accounts, K8s clusters, GitHub integration, and workspaces. Runs entirely
on docker compose; zero licensing cost; no SaaS dependency.

## Repo Map (one-liners)
| Path | What |
|---|---|
| `services/api/` | FastAPI orchestrator. Auth, RBAC, workspaces, runs, approvals, drift, audit. |
| `services/ui/` | React + Vite + Tailwind UI. Dashboard, Runs, Approvals, Settings. |
| `services/executor/` | Container image launched per run; runs `terraform plan/apply`. `Dockerfile.helm` is the Helm variant (helm/kubectl/helm-diff/aws-cli) for `kind=helm` workspaces. |
| `services/drift-detector/` | Periodic job: compares live AWS state vs TF state. |
| `services/liveness-detector/` | Simple health watchdog. |
| `terraform/` | Example workspace tree (account / region / leaf hierarchy). |
| `forgejo/` | Self-hosted Gitea-fork for repos + CI (act_runner). |
| `policies/` | Checkov / OPA policy bundles run during plan. |
| `scripts/` | `onboard.sh`, `seed-dev-users.sh`, `init-db.sh`, load-test. |
| `docs/` | Human docs (architecture, API, onboarding, disaster recovery). |

## Service Topology (read this once)
```
                          ┌─ traefik (80/443/8080)
                          │
 browser ──► ui (3001) ──►│
                          ├─► api (8001) ──► postgres (internal 5432)
                          │                  │
                          │                  └─► localstack S3 (4566) for tfstate
                          │
                          ├─► forgejo (3002) ──► act_runner
                          │
 api ──launch──► executor container ──► terraform plan/apply ──► S3 + audit
 drift-detector loop ──► executor (read-only plan) ──► drift_reports
```

## Quick Commands
```bash
make up                                    # docker compose up -d --wait (waits for healthchecks)
make down                                  # stop everything
make seed-db                               # admin@test.com / password123
make logs                                  # docker compose logs -f (all services)
make test-api                              # pytest in services/api
make test-ui                               # vitest + playwright in services/ui
docker compose up -d --build ui            # rebuild just the UI after FE change
docker compose up -d --build api           # rebuild just the API after BE change
```
Login URL: http://localhost:3001  ·  API: http://localhost:8001  ·  Forgejo: http://localhost:3002

## Conventions Claude Must Follow
- **Don't create .env files.** All runtime config lives in the encrypted Postgres
  `config` table. The only env vars are `DATABASE_URL` and `CREDENTIAL_ENCRYPTION_KEY`.
  Config changes take effect within ~60s (TTL cache) without restart.
- **Workspace deletes:** UI hides Delete for git-synced workspaces; API returns
  409 if you try to delete one (`repo_url` set and not `local://`). Only manual
  / local-only workspaces are normally deletable. Git-synced ones are pruned by
  removing the path from the source repo + waiting for the periodic repo-sync
  loop (or hitting **Sync from repo**) to flip them to `path_status=orphaned`,
  at which point the UI surfaces a **Force delete** button (and the API honors
  `DELETE /v1/workspaces/{id}?force=true&delete_state=…`). The force path is a
  pure DB/state cleanup — it does NOT run a real terraform destroy, so the AWS
  resources must already be migrated or removed out of band. See the
  Workspaces section of `docs/ARCHITECTURE.md`.
- **Business Units (multi-tenancy):** Each `workspace` / `aws_account` belongs
  to exactly one BU (`business_unit_id` FK). The UI sends `X-Business-Unit:
  <slug>` on every request; the backend filters list endpoints and stamps the
  current BU on creates. Superadmins (`users.is_superadmin=true`) bypass the
  filter (header `all` → see everything). Non-superadmins are members of one
  or more BUs via `user_business_units` with a per-BU `role` (operator |
  viewer). The legacy `users.role` column is still populated for one release
  as a fallback. See the Business Units section of `docs/ARCHITECTURE.md`.
- **State backend:** pluggable per workspace via `workspaces.state_backend`
  (`s3` default | `azureblob` | `gcs`), keyed by `{tf_working_dir}/terraform.tfstate`.
  The executor always talks Terraform's `backend "http"` to the API; the API is
  the only component that touches the object store, selecting the backend in
  `routers/state.py:_service_for` (S3/Azure Blob/GCS implement the `StateStore`
  protocol in `services/state_store.py`). `s3` uses a bucket dedicated to the
  workspace's AWS account (LocalStack in dev); `azureblob` reuses the linked
  Azure SP against a storage account/container; `gcs` reuses the linked GCP
  project's SA key against a bucket. Locking is DB-side (`pg_advisory_xact_lock`),
  backend-independent — not DynamoDB.
- **Cloud providers:** AWS (`aws_accounts`), Azure (`azure_subscriptions`,
  `azurerm` via `ARM_*`), and GCP (`gcp_projects`, `google` via a service-account
  key → `GOOGLE_APPLICATION_CREDENTIALS`). Each is a per-BU-scoped vertical slice
  (model/schema/service/router + a `CloudProviders` UI tab); a workspace links to
  one via `aws_account_id` / `azure_subscription_id` / `gcp_project_id`.
- **No new sky-* colors.** Sky in Tailwind is aliased to brand teal; new code
  should write `brand-*` / `accent-*` directly.
- **Migrations are forward-only.** Alembic; one revision per change; never edit
  a merged migration's `upgrade()`/`downgrade()` logic. The sole exception is a
  one-time, comment/docstring-only redaction of leaked secrets or identifiers
  (e.g. this repo's OSS-sanitization pass) — never schema-affecting code.
- **Secrets never leave the API.** GET endpoints for integrations return
  `{configured: bool, masked_tail}` — never the plaintext token.

## Roles (RBAC)
`viewer < operator < admin`. `require_role(Role.admin)` gates destructive admin
endpoints (user mgmt, workspace delete, AWS account delete). `operator` can
trigger runs / approve / disable webhooks. `viewer` is read-only. Source of
truth: `services/api/app/auth/rbac.py`.

**Superadmin** is a cross-BU flag (`users.is_superadmin`), not a role. It sees
all BUs, all workspaces, all accounts. Use it to bypass BU scoping for
break-glass / ops. Set via `seed_dev_users.py` (admin@test.com gets it) or via
`PATCH /api/v1/users/{id}` (superadmin-only, interactive-only — API keys are
rejected regardless of capability tier). See the Business Units section of
`docs/ARCHITECTURE.md`.

> Note: 4-eyes approval was revoked. Any operator may approve any run in their
> BU; the triggering user is no longer excluded. Don't reintroduce a 4-eyes
> check without explicit approval.

## Default Dev Users (seeded by `make seed-db`)
| Email | Password | Role |
|---|---|---|
| `admin@test.com` | `password123` | admin |
| `operator@test.com` | `password123` | operator |
| `viewer@test.com` | `password123` | viewer |

## When Editing
1. Read the relevant section of `docs/ARCHITECTURE.md` or `docs/API.md` before changing code.
2. If you change a router, model, page, or migration: update those docs to match.
3. Run the matching test target (`test-api` for backend, `test-ui` for frontend).
4. Rebuild only the affected container (`docker compose up -d --build <svc>`).

## Do Not
- Add new env vars. Use the `config` table + `ConfigService` (per-BU keys
  should be namespaced `bu.<slug>.<rest>` once that lands).
- Add Dynamo / external state lock. Postgres advisory locks are the design.
- Bypass `require_role` on a write endpoint.
- Hard-code colors. Use Tailwind tokens (`brand-*`, `accent-*`, `td-*` CSS vars).
- Edit a merged Alembic revision. Add a new one instead.
- Delete git-synced workspaces. They will resync. Remove them in the source repo.
- Stamp new `workspaces` / `aws_accounts` rows without a `business_unit_id`.
  The column is NOT NULL — derive from the `current_bu` dependency in routes.
