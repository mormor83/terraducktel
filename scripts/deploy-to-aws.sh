#!/usr/bin/env bash
# Build + push TerraDuckTel container images to ECR.
#
# This script no longer runs `terraform apply` — the ECS task-def rollover
# is owned by a separate terraform-infra repo, so we can build images
# without touching infrastructure. After this script finishes, run the
# matching terraform apply over there (or update task defs by whatever
# your deploy flow is) to roll the services onto the new tag.
#
# Prerequisites:
#   - AWS_PROFILE (or other AWS creds) resolve to the target account
#   - docker + docker buildx are available
#   - the ECR repos already exist (created by the terraform stack)
#
# Configuration (env vars; defaults shown):
#   AWS_PROFILE   (default)  — uses your default AWS profile if unset
#   AWS_REGION    us-east-1
#   ACCOUNT_ID    (required) — 12-digit AWS account that owns the ECR repos
#
# Usage:
#   ACCOUNT_ID=123456789012 ./scripts/deploy-to-aws.sh           # default tag
#   ACCOUNT_ID=123456789012 ./scripts/deploy-to-aws.sh v0.2.0    # custom tag
#
# Safety: build + push only. No infra changes. The tag is printed at the end
# for the operator to plug into the terraform apply step that follows.

set -euo pipefail

# ─── config ────────────────────────────────────────────────────────────────

AWS_PROFILE="${AWS_PROFILE:-default}"
AWS_REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="${ACCOUNT_ID:-}"
if [[ -z "$ACCOUNT_ID" ]]; then
  echo "ACCOUNT_ID env var is required (12-digit AWS account id)" >&2
  exit 2
fi
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
PLATFORM="linux/amd64"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Tag: positional arg wins; otherwise derive from current git HEAD.
TAG="${1:-v0.1.0-$(git -C "$REPO_ROOT" rev-parse --short HEAD)}"

SERVICES=(api ui drift-detector liveness-detector executor)

# ─── helpers ───────────────────────────────────────────────────────────────

step() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
err()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

export AWS_PROFILE

# ─── 1. preflight ──────────────────────────────────────────────────────────

step "Preflight (AWS profile=${AWS_PROFILE}, region=${AWS_REGION})"
WHOAMI=$(aws sts get-caller-identity --output json)
echo "$WHOAMI" | python3 -c 'import sys,json;d=json.load(sys.stdin);print("  account:",d["Account"],"\n  arn:    ",d["Arn"])'
ACTUAL_ACCOUNT=$(echo "$WHOAMI" | python3 -c 'import sys,json;print(json.load(sys.stdin)["Account"])')
[[ "$ACTUAL_ACCOUNT" == "$ACCOUNT_ID" ]] || err "wrong account: got $ACTUAL_ACCOUNT, expected $ACCOUNT_ID"

step "ECR repos check"
EXPECTED="terraducktel/api terraducktel/ui terraducktel/drift-detector terraducktel/liveness-detector terraducktel/executor"
PRESENT=$(aws ecr describe-repositories --region "$AWS_REGION" \
  --query 'repositories[?starts_with(repositoryName, `terraducktel/`)].repositoryName' --output text)
for r in $EXPECTED; do
  echo "$PRESENT" | tr '[:space:]' '\n' | grep -qx "$r" || err "missing ECR repo: $r — run terraform apply on the stack first"
done
echo "  all 5 repos present"

# ─── 2. ECR login ──────────────────────────────────────────────────────────

step "Docker login to ${REGISTRY}"
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null
echo "  logged in"

# ─── 3. buildx setup ───────────────────────────────────────────────────────

step "buildx (multi-arch builder)"
if ! docker buildx inspect tdt-builder >/dev/null 2>&1; then
  docker buildx create --name tdt-builder --use
else
  docker buildx use tdt-builder
fi
docker buildx inspect --bootstrap | sed -n '1,4p'

# ─── 4. build + push, one service at a time ────────────────────────────────

for svc in "${SERVICES[@]}"; do
  step "Build + push ${svc} → ${REGISTRY}/terraducktel/${svc}:${TAG}"
  CTX="$REPO_ROOT/services/$svc"
  [[ -d "$CTX" ]] || err "missing build context: $CTX"
  docker buildx build \
    --platform "$PLATFORM" \
    --tag "${REGISTRY}/terraducktel/${svc}:${TAG}" \
    --provenance=false \
    --push \
    "$CTX"
done

# ─── 5. done ───────────────────────────────────────────────────────────────

step "Build + push complete. Tag: ${TAG}"
echo
echo "  Images pushed to ${REGISTRY}/terraducktel/<svc>:${TAG} for:"
for svc in "${SERVICES[@]}"; do
  echo "    - ${svc}"
done
echo
echo "  Next step: roll the ECS task definitions over to this tag from the"
echo "  terraform-infra repo, e.g.:"
echo
echo "    cd ../terraform-infra/account-${ACCOUNT_ID}/${AWS_REGION}/Terraducktel"
echo "    terraform apply -var image_tag=${TAG}"
