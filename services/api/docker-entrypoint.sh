#!/usr/bin/env sh
# API container entrypoint. Runs schema migrations and (optionally) seeds dev
# users before handing off to uvicorn. Both steps are idempotent:
#   - `alembic upgrade head` is a no-op once the DB is at head.
#   - seed_dev_users.py uses INSERT … ON CONFLICT DO NOTHING semantics, so
#     running it twice doesn't duplicate or overwrite anything.
#
# Knobs (env vars, all optional):
#   TDT_RUN_MIGRATIONS         (default: true)  — alembic upgrade head on boot
#   TDT_BOOTSTRAP_SEED_USERS   (default: false) — also seed admin/operator/
#                                                 viewer, with a fresh random
#                                                 password per user (printed
#                                                 once below — capture it from
#                                                 the deploy logs). Flip to
#                                                 true on the first prod
#                                                 deploy, then back to false
#                                                 (or omit) afterwards. Never
#                                                 uses the well-known
#                                                 "password123" dev password —
#                                                 that's local-dev-only, set
#                                                 by `make seed-db` instead.
#
# Exit early on any failure so the ECS deployment circuit breaker rolls back
# instead of running a half-migrated DB.

set -eu

run_migrations="${TDT_RUN_MIGRATIONS:-true}"
seed_users="${TDT_BOOTSTRAP_SEED_USERS:-false}"

if [ "$run_migrations" = "true" ]; then
  echo "[entrypoint] alembic upgrade head"
  alembic upgrade head
fi

if [ "$seed_users" = "true" ]; then
  echo "[entrypoint] seeding admin/operator/viewer with random passwords (see below)"
  echo "[entrypoint] DELETE THIS FLAG FROM THE TASK DEF AFTER FIRST DEPLOY"
  SEED_RANDOM_PASSWORDS=true python scripts/seed_dev_users.py
fi

echo "[entrypoint] starting uvicorn"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
