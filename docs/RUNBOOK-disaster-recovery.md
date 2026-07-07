# Disaster Recovery Runbook — Terraducktel

Audience: an on-call engineer who has not touched TDT before. Goal: get back
to a working stack from cold storage in under 30 minutes.

## What is backed up

| Asset | Where | How | RPO | RTO |
|---|---|---|---|---|
| Postgres (workspaces, runs, audit, config) | `pg-backups` named volume on the host | `services/pg-backup` sidecar — `pg_dump` every 6h, 7-day retention | 6h | < 10 min |
| Terraform state (`tfstate/...`) | S3 bucket (LocalStack in dev) | Native S3 versioning (must be enabled on the bucket) | latest write | ~5 min |
| Encryption seed (`CREDENTIAL_ENCRYPTION_KEY`) | external secret store (Vault / AWS SM / `.envrc`) | manual rotation via `services/api/scripts/reencrypt_aws_creds.py` | n/a | n/a |
| Audit log | inside the Postgres dump | append-only, hash-chained (see `app/services/audit_chain.py`) | 6h | n/a |

Audit log integrity is **independent of the dumps**: even if a recovered dump
is older than the latest events, `GET /api/v1/audit/verify` will still report
`ok: true` because the chain starts at the empty hash and walks forward.

## Recovery scenarios

### 1) Single service died (api, ui, drift-detector, …)
```bash
docker compose up -d --force-recreate <service>
docker compose logs -f <service>
```
No data loss. The job queue (`run_jobs`) survives in Postgres; the reaper will
auto-fail any runs that had no heartbeat for 90s and release their state-locks.

### 2) API host lost; Postgres + state bucket intact
Spin up a fresh host with the same code checkout.
```bash
# Bring up everything; alembic upgrade head runs at API container boot.
make up
make seed-db   # only if users table was wiped; harmless if not
```
Existing runs in non-terminal states will be reaped within 90s.

### 3) Postgres host lost; backups + state bucket intact
1. Stand up a fresh `postgres` container (or RDS instance) with the same major version (16).
2. Identify the most recent dump:
   ```bash
   docker compose run --rm --entrypoint sh pg-backup -c 'ls -lt /backups | head -5'
   ```
3. Restore:
   ```bash
   docker compose up -d postgres
   docker compose exec -T postgres createdb -U terraducktel terraducktel || true
   docker compose run --rm --entrypoint sh pg-backup -c \
     'gunzip -c /backups/tdt-<TIMESTAMP>.sql.gz | psql -h postgres -U terraducktel -d terraducktel'
   ```
4. Bring up the rest of the stack: `make up`.
5. Verify:
   ```bash
   TOKEN=$(curl -sS -X POST http://localhost:8001/api/v1/auth/token \
     -H 'Content-Type: application/json' \
     -d '{"email":"admin@test.com","password":"password123"}' | jq -r .access_token)
   curl -sS -H "Authorization: Bearer $TOKEN" http://localhost:8001/api/v1/audit/verify
   ```
   Expected: `{"ok": true, ...}`. If `broken_at` is non-empty, an attacker may
   have tampered with the audit log between the last successful dump and the
   crash — escalate to security.

### 4) State bucket lost
This is the worst case. Postgres still knows which workspaces exist but the
actual `terraform.tfstate` is gone. Options:

- **If S3 versioning was enabled** (highly recommended; not enforced in code today):
  use AWS console or `aws s3api list-object-versions` to recover the most recent
  good version of each `tfstate/{account}/{region}/{env}/{workspace}/terraform.tfstate`.
- **If versioning was off**: you must `terraform import` each resource back into
  state from cloud reality. Painful but possible.

### 5) Encryption key lost
Without `CREDENTIAL_ENCRYPTION_KEY`, you cannot decrypt:
- AWS account credentials in the `aws_accounts` table.
- The OIDC client secret + any integration tokens in the `config` table.

The workspaces, runs, and audit log are still readable (those aren't
encrypted). To recover:
1. Generate a new key.
2. Set the new key in compose (or via your secret manager) and start the API.
3. Re-add AWS account credentials and integration tokens through the UI; old
   encrypted blobs are permanently lost.

**The key MUST be stored outside the TDT stack** (Vault, AWS SM, 1Password,
sealed envelope). The pg-backup sidecar does NOT back it up.

## Smoke tests after recovery
1. `curl http://localhost:8001/health` → 200.
2. Log in to `http://localhost:3001`.
3. Trigger a `plan` on a known workspace; it should reach `awaiting_approval` within ~60s.
4. `curl /api/v1/audit/verify` → `ok: true`.
5. Check `/metrics`:
   - `tdt_run_queue_depth_gauge` should fall to 0 within a minute of the test plan completing.
   - `tdt_drift_age_seconds_gauge` should be < 30 minutes (drift-detector is alive).

## Quarterly game-day
Once a quarter, deliberately blow away the Postgres volume on a non-prod host
and walk through scenario 3 from scratch. Time-box to 30 minutes. If you blow
the time budget, write the friction points back into this runbook.

## Pager triggers (suggested)
- `tdt_executor_failures_total` rate > 5/min for 10 min.
- `tdt_run_queue_depth_gauge` > 20 for 10 min.
- `tdt_approval_pending_seconds_gauge` > 14400 (4h).
- `tdt_drift_age_seconds_gauge` > 3600 (drift-detector wedged).
- pg-backup container missing a heartbeat for > 8h (no new `tdt-*.sql.gz` file).
