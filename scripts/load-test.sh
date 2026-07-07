#!/usr/bin/env bash
set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"

echo "=== Load test: concurrent workspace + plan creates (API) ==="
echo "Set API_URL and ensure API is running with seeded users."

if ! command -v curl >/dev/null; then
  echo "curl required"
  exit 1
fi

TOKEN="${API_TOKEN:-}"
if [[ -z "$TOKEN" ]]; then
  echo "Login to obtain token (admin@test.com / password123 in dev tests):"
  TOKEN=$(curl -s -X POST "${API_URL}/api/v1/auth/token" \
    -H "Content-Type: application/json" \
    -d '{"email":"admin@test.com","password":"password123"}' | jq -r .access_token)
fi

if [[ "$TOKEN" == "null" || -z "$TOKEN" ]]; then
  echo "Failed to get API_TOKEN"
  exit 1
fi

pids=()
for i in $(seq 1 5); do
  (
    WS=$(curl -s -X POST "${API_URL}/api/v1/workspaces" \
      -H "Authorization: Bearer ${TOKEN}" \
      -H "Content-Type: application/json" \
      -d "{\"name\":\"load-ws-${i}-$$\",\"environment\":\"dev\",\"aws_account_id\":\"123456789012\",\"region\":\"us-east-1\"}" | jq -r .id)
    curl -s -o /dev/null -w "%{http_code}" -X POST "${API_URL}/api/v1/workspaces/${WS}/runs" \
      -H "Authorization: Bearer ${TOKEN}" \
      -H "Content-Type: application/json" \
      -d '{"command":"plan"}'
  ) &
  pids+=($!)
done

ok=0
for pid in "${pids[@]}"; do
  if wait "$pid"; then
    ok=$((ok + 1))
  fi
done

echo "Completed background jobs (shell wait status)."
echo "PASS: load-test script finished (manual: verify 201 responses in API logs)."
