#!/usr/bin/env bash
set -euo pipefail

MAX_SEC="${ONBOARD_MAX_SEC:-1800}"
START=$(date +%s)

if [[ -x "$(dirname "$0")/onboard.sh" ]]; then
  "$(dirname "$0")/onboard.sh"
else
  echo "onboard.sh not found or not executable"
  exit 1
fi

END=$(date +%s)
ELAPSED=$((END - START))
echo "Onboarding elapsed: ${ELAPSED}s (max ${MAX_SEC}s)"
if [[ "$ELAPSED" -gt "$MAX_SEC" ]]; then
  echo "FAIL: exceeded max time"
  exit 1
fi
echo "PASS: within limit"
