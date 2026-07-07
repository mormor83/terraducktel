#!/usr/bin/env bash
# Apply migrations and seed dev users (admin/operator/viewer — password123).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if ! docker compose ps api --status running --quiet 2>/dev/null | grep -q .; then
  echo "ERROR: api container is not running. Start the stack: docker compose up -d"
  exit 1
fi
docker compose exec -T api sh -c 'cd /app && alembic upgrade head && python scripts/seed_dev_users.py'
