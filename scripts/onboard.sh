#!/usr/bin/env bash
set -euo pipefail

START_TIME=$(date +%s)

echo "=== Terraducktel — onboarding (dev) ==="
echo "Prerequisites: Docker with compose plugin."

if ! command -v docker >/dev/null; then
  echo "ERROR: docker not found"
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]] && [[ -f .env.example ]]; then
  echo "Copying .env.example to .env — edit secrets for production."
  cp .env.example .env
fi

echo ""
echo "[1/4] Building and starting Docker Compose stack..."
docker compose build --quiet
docker compose up -d --wait

echo ""
echo "[2/4] Creating S3 state bucket in LocalStack..."
docker compose exec -T localstack \
  awslocal s3 mb s3://terraducktel-state 2>/dev/null || echo "  (bucket already exists)"

echo ""
echo "[3/4] Running database migrations..."
docker compose exec -T api python -m alembic upgrade head

echo ""
echo "[4/4] Seeding development users..."
docker compose exec -T api python scripts/seed_dev_users.py

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

echo ""
echo "=== Onboarding complete in ${ELAPSED}s ==="
echo ""
echo "  UI:       http://localhost:3001"
echo "  API docs: http://localhost:8001/docs"
echo "  Forgejo:  http://localhost:3002"
echo "  Traefik:  http://localhost:18080"
echo ""
echo "  Logins:   admin@test.com / operator@test.com / viewer@test.com"
echo "  Password: password123"
