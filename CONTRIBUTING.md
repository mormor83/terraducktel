# Contributing to Terraducktel

Thanks for considering a contribution. Terraducktel (TDT) is a self-hosted
Terraform/Helm orchestration platform — see [`README.md`](README.md) for what
it does and [`CLAUDE.md`](CLAUDE.md) + [`docs/`](docs/) for the deeper
architecture reference. This file covers the mechanics of contributing.

## Dev environment

Everything runs via `docker compose`. There is no separate "local" mode.

```bash
make up          # docker compose up -d --wait — builds and starts every service
make seed-db     # seeds default dev users (admin@test.com / password123, etc.)
make logs        # tail all container logs (docker compose logs -f)
make down        # stop everything
```

Once up: UI at `http://localhost:3001`, API at `http://localhost:8001`,
Forgejo (git + CI) at `http://localhost:3002`.

To rebuild a single service after a change:

```bash
docker compose up -d --build ui    # frontend change
docker compose up -d --build api   # backend change
```

**Do not add `.env` files.** Runtime configuration lives in the encrypted
Postgres `config` table, managed through `ConfigService`. The only env vars
that exist are `DATABASE_URL` and `CREDENTIAL_ENCRYPTION_KEY`. Config edits
made through the UI/API take effect within ~60 seconds (TTL cache) — no
restart needed. If your change needs a new setting, add a config key, not an
env var.

## Running tests

Backend (FastAPI, pytest) — from `services/api`:

```bash
make test-api
# equivalent to: cd services/api && python -m pytest tests/ -v
```

Integration tests (slower, marked `integration`):

```bash
make test-integration
```

Frontend (Vite + Vitest unit tests, Playwright e2e) — from `services/ui`:

```bash
make test-ui
# equivalent to: cd services/ui && npm run test:e2e
```

Run `make lint` (pre-commit: terraform fmt/validate/checkov/trivy plus
whitespace/YAML/JSON hygiene) and `make scan` (trivy + checkov against the
repo) before opening a PR if you touched Terraform fixtures or policy bundles.

## Coding conventions

These come directly from the project's internal conventions — please follow
them:

- **No new environment variables.** All runtime config belongs in the
  Postgres `config` table via `ConfigService`. Per-BU settings are namespaced
  `bu.<slug>.<rest>`.
- **Migrations are forward-only.** We use Alembic. One revision per logical
  change. Never edit a migration that has already been merged — add a new
  revision instead, even to fix a mistake in a previous one.
- **No hardcoded colors.** Use the Tailwind design tokens (`brand-*`,
  `accent-*`, `td-*` CSS variables). Don't introduce new `sky-*` usages —
  `sky` is aliased to brand teal; write `brand-*`/`accent-*` directly instead.
- **Business Unit (BU) scoping.** `workspace` and `aws_account` rows each
  belong to exactly one BU (`business_unit_id`, `NOT NULL`). New rows must
  derive their BU from the request's `current_bu` dependency — never leave it
  unset or hardcode one. List/read endpoints must filter by the caller's BU
  membership; only `is_superadmin` users bypass that filter.
- **RBAC role hierarchy:** `viewer < operator < admin`. Gate destructive or
  admin-only endpoints with `require_role(Role.admin)` (user management,
  workspace delete, AWS account delete). `operator` can trigger runs, approve
  runs, and manage webhooks. `viewer` is strictly read-only. Never bypass
  `require_role` on a write endpoint. Note: 4-eyes approval has been removed —
  any operator (including the run's own triggering user) may approve a run in
  their BU; don't reintroduce a self-approval restriction without discussion.
- **Don't delete git-synced workspaces via the API.** Workspaces backed by a
  `repo_url` (not `local://`) are only removable by deleting the path in the
  source repo and letting the sync loop mark them `orphaned`, after which a
  force-delete is available. Only local/manual workspaces are directly
  deletable.
- **Secrets never leave the API process.** Integration GET endpoints return
  `{configured: bool, masked_tail}` — never plaintext tokens/keys. Don't add
  an endpoint that echoes a stored credential back to the client.

## Before opening a PR

- [ ] Ran the relevant test suite (`make test-api` and/or `make test-ui`) and
      it passes locally.
- [ ] If you touched a router, model, page, or migration, updated
      `docs/ARCHITECTURE.md` and/or `docs/API.md` to match.
- [ ] New Alembic revisions are additive — no edits to already-merged
      migrations.
- [ ] No new env vars; no hardcoded colors; no `sky-*` usage.
- [ ] Any new `workspaces`/`aws_accounts` row-creation path stamps
      `business_unit_id` from the request context.
- [ ] Write endpoints are behind the correct `require_role` check.
- [ ] Rebuilt and smoke-tested the affected container(s) locally
      (`docker compose up -d --build <service>`).
- [ ] PR description explains the *why*, not just the *what*, and links any
      relevant issue.

## Reporting bugs / requesting features

Open a GitHub issue with clear repro steps (for bugs) or the motivating use
case (for features). For security issues, see [`SECURITY.md`](SECURITY.md) —
please don't file those as public issues.
