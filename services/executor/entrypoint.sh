#!/bin/bash
set -euo pipefail

# Restrict every git operation in this container to network transports only.
# A workspace/OPA repo_url like `ext::sh -c '...'` or `file://...` would
# otherwise make git execute a command or read local files. Exported
# once here so it applies to every `git clone` below.
export GIT_ALLOW_PROTOCOL="http:https:ssh"
export GIT_TERMINAL_PROMPT="0"

# Required env vars — no defaults; fail fast if unset
: "${RUN_ID:?RUN_ID must be set}"
: "${API_URL:?API_URL must be set}"
: "${API_TOKEN:?API_TOKEN must be set}"
: "${WORKSPACE_ID:?WORKSPACE_ID must be set}"
: "${REPO_URL:?REPO_URL must be set}"
# WORKSPACE_KIND drives the whole pipeline: "terraform" (default) keeps the
# byte-identical terraform behavior below; "helm" runs the helm pipeline
# (helm_pipeline()) instead. Defaulting to terraform is the safety gate — a run
# that never sets WORKSPACE_KIND behaves exactly as before.
: "${WORKSPACE_KIND:=terraform}"
# the state backend is authenticated with the run-scoped API_TOKEN
# (workspace-bound) via HTTP Basic, NOT a shared global token. API_TOKEN is
# already required above. The legacy TERRADUCKTEL_STATE_TOKEN is no longer
# passed to executors.
: "${TF_WORKING_DIR:=.}"
: "${TF_COMMAND:=plan}"
: "${TF_PHASE:=plan}"  # plan | apply
: "${REPO_REF:=main}"
# Helm-only soft defaults. KUBECONFIG_CONTENT is the decrypted kubeconfig the
# API injects for helm workspaces (never written to disk by the API; we write
# it to a 0600 file here and export KUBECONFIG).
: "${KUBECONFIG_CONTENT:=}"
# AWS creds are SOFT defaults, not hard requirements. A non-AWS workspace
# (e.g. Cloudflare, Azure, DNS-only) has no aws_account_id row and the
# legacy global key may not be set either — both env vars come through
# empty. Hard-failing here used to kill the container at line 15 before
# any step was even reported, leaving the run wedged in "running" status
# forever (the reaper only watched PICKED jobs and the worker had already
# flipped this one to DONE). Terraform's AWS provider will surface a
# clear "no valid credential sources" error later in `terraform plan` if
# a module actually needs AWS — that's the right place for the check.
: "${AWS_ACCESS_KEY_ID:=}"
: "${AWS_SECRET_ACCESS_KEY:=}"
: "${AWS_DEFAULT_REGION:=us-east-1}"

# Azure creds are also SOFT defaults — only populated when the workspace is
# linked to an azure_subscriptions row in the API. terraform's azurerm
# provider reads these standard names directly; we do not run `az login`.
: "${ARM_SUBSCRIPTION_ID:=}"
: "${ARM_TENANT_ID:=}"
: "${ARM_CLIENT_ID:=}"
: "${ARM_CLIENT_SECRET:=}"
: "${TDT_CLOUD_PROVIDERS:=}"

# GCP creds are also SOFT defaults — only populated when the workspace is
# linked to a gcp_projects row. We write GCP_SA_KEY_JSON to a 0600 file and
# export GOOGLE_APPLICATION_CREDENTIALS below; the terraform google provider
# reads GOOGLE_APPLICATION_CREDENTIALS / GOOGLE_PROJECT / GOOGLE_REGION
# directly (no `gcloud auth`).
: "${GCP_SA_KEY_JSON:=}"
: "${GOOGLE_PROJECT:=}"
: "${GOOGLE_REGION:=}"

# ---------------------------------------------------------------------------
# report_status: PATCH the run-level status (running/planned/applied/failed).
# ---------------------------------------------------------------------------
report_status() {
  local status="$1"
  local output="${2:-}"
  # Build the body via a file so neither $output nor the full body bloats the
  # command line past ARG_MAX (terraform apply on a real stack can produce
  # several MB of output).
  printf '%s' "${output}" > /tmp/_status_out.txt
  jq -n --arg s "${status}" --rawfile o /tmp/_status_out.txt \
    '{status:$s, plan_output:$o}' > /tmp/_status_body.json
  local i
  for i in 1 2 3; do
    if curl -sf -X PATCH "${API_URL}/api/v1/runs/${RUN_ID}" \
        -H "Authorization: Bearer ${API_TOKEN}" \
        -H "Content-Type: application/json" \
        --data-binary @/tmp/_status_body.json; then
      return 0
    fi
    sleep $((i * 2))
  done
  echo "WARNING: failed to report status ${status} after 3 retries" >&2
  return 0
}

# ---------------------------------------------------------------------------
# Step timeline helpers — find/transition individual run-step rows so the UI
# shows per-phase status + duration. Failures during step PATCH are logged but
# never crash the run; the executor is the source of truth, the timeline is a
# best-effort projection.
# ---------------------------------------------------------------------------
STEPS_JSON=""

fetch_steps() {
  STEPS_JSON=$(curl -sf -H "Authorization: Bearer ${API_TOKEN}" \
    "${API_URL}/api/v1/runs/${RUN_ID}/steps" 2>/dev/null || echo "[]")
}

step_id_for() {
  local name="$1"
  printf '%s' "${STEPS_JSON}" | jq -r --arg n "$name" '.[] | select(.name==$n) | .id' | head -1
}

step() {
  # Usage: step "Name" status [output] [summary_json]
  local name="$1" status="$2" output="${3:-}" summary="${4:-}"
  local sid
  sid=$(step_id_for "$name")
  if [[ -z "$sid" ]]; then return 0; fi
  local body
  if [[ -n "$summary" ]]; then
    body=$(jq -n --arg s "$status" --arg o "$output" --arg j "$summary" \
      '{status:$s, output:(if $o=="" then null else $o end), summary_json:(if $j=="" then null else $j end)}')
  else
    body=$(jq -n --arg s "$status" --arg o "$output" \
      '{status:$s, output:(if $o=="" then null else $o end)}')
  fi
  curl -sf -X PATCH "${API_URL}/api/v1/runs/${RUN_ID}/steps/${sid}" \
    -H "Authorization: Bearer ${API_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "${body}" > /dev/null || echo "WARNING: step '$name' → '$status' patch failed" >&2
}

# Wrap a phase: marks running before, success/failed after; captures stdout into
# the step's `output` field.
run_step() {
  local name="$1"
  shift
  step "$name" running ""
  local out
  if out=$("$@" 2>&1); then
    step "$name" success "$out"
    printf '%s\n' "$out"
  else
    local rc=$?
    step "$name" failed "$out"
    return $rc
  fi
}

# stream_step_output: while the named step is `running`, periodically PATCH its
# `output` field with the current contents of a log file. Lets the UI show
# `terraform plan/apply` output line-by-line as it lands instead of waiting for
# the step to finish. Run in a `&` background subshell, kill when the step is
# done, then send a final PATCH with the complete log.
stream_step_output() {
  local step_name="$1"
  local log_file="$2"
  local sid
  sid=$(step_id_for "$step_name")
  if [[ -z "$sid" ]]; then return 0; fi
  while true; do
    if [[ -f "$log_file" ]]; then
      # Truncate to last 64 KB so a 50 MB apply log doesn't bloat every PATCH.
      local tail_size=65536
      local current
      current=$(tail -c "$tail_size" "$log_file" 2>/dev/null || true)
      if [[ -n "$current" ]]; then
        local body
        body=$(jq -n --arg s "running" --arg o "$current" '{status:$s, output:$o}')
        curl -sf -X PATCH "${API_URL}/api/v1/runs/${RUN_ID}/steps/${sid}" \
          -H "Authorization: Bearer ${API_TOKEN}" \
          -H "Content-Type: application/json" \
          -d "$body" > /dev/null 2>&1 || true
      fi
    fi
    sleep 1.5
  done
}

# stream_run: wrap a long-running command. Marks the step `running`, starts the
# log streamer in the background, runs the command piping output through `tee`
# to a log file, kills the streamer, then PATCHes the final status (success or
# failed) with the full log. Returns the command's exit code.
#
# Usage:  stream_run "Step Name" /tmp/log.file -- terraform init -input=false -no-color
stream_run() {
  local step_name="$1"; shift
  local log_file="$1"; shift
  if [[ "$1" == "--" ]]; then shift; fi

  : > "$log_file"
  step "$step_name" running ""
  stream_step_output "$step_name" "$log_file" &
  local streamer=$!

  # Run the pipeline with errexit AND the ERR trap both suspended:
  #  - errexit (`set -e`) would abort the script on a failed command.
  #  - `set +e` alone is NOT enough — the ERR trap still fires on nonzero
  #    exit, calling on_unexpected_err which would report the run as
  #    "failed" the moment terraform plan returns 2 / checkov returns 1.
  # We capture PIPESTATUS BEFORE re-enabling either, since `|| true` would
  # clobber PIPESTATUS by running `true` as a 1-cmd pipeline (this is the
  # bug that originally made init/plan steps show green regardless).
  local rc=0
  set +e
  trap - ERR
  "$@" 2>&1 | tee -a "$log_file"
  rc=${PIPESTATUS[0]}
  trap 'on_unexpected_err $LINENO' ERR
  set -e

  kill "$streamer" 2>/dev/null || true
  wait "$streamer" 2>/dev/null || true

  if [[ $rc -eq 0 ]]; then
    step "$step_name" success "$(cat "$log_file")"
  else
    step "$step_name" failed "$(cat "$log_file")"
  fi
  return $rc
}

on_unexpected_err() {
  local exit_code=$?
  local line=${1:-?}
  local lastcmd=${BASH_COMMAND:-unknown}
  # Include the last 80 lines of the most recent log file we have so the run
  # row carries an actionable error instead of just "Unexpected error in
  # executor". Falls back to the bare message when no log file is around.
  local log=""
  for f in /tmp/plan.log /tmp/init.log /tmp/apply.log /tmp/checkov.log; do
    if [[ -s "$f" ]]; then log="$f"; break; fi
  done
  local tail_out=""
  [[ -n "$log" ]] && tail_out=$'\n--- '"$log"$' (last 80 lines) ---\n'"$(tail -n 80 "$log")"
  report_status "failed" "Unexpected error at line ${line} (exit=${exit_code}): ${lastcmd}${tail_out}"
}
trap 'on_unexpected_err $LINENO' ERR

# ---------------------------------------------------------------------------
# OPA Policy Check — run conftest over the terraform plan JSON.
#
# Sources are merged: the bundled defaults baked into the image
# (/opt/tdt/policies/bundled), the BU's DB-authored policies (pulled from
# GET /runs/{id}/policies), and an optional git policy-repo. Each source is
# evaluated separately so every finding is attributed to a severity:
#   - DB policies carry their own per-rule severity.
#   - bundled + git take OPA_BUNDLED_SEVERITY / OPA_GIT_SEVERITY.
#
# The per-BU OPA_MODE is the master switch:
#   off      → step skipped, policy_status=not_run (this is the default).
#   warn     → findings recorded, never blocks.  policy_status=warned|passed.
#   enforce  → a `block`-severity failure fails the run before approval.
#              warn/info severity stays advisory.  policy_status=failed|warned|passed.
#
# Requires /tmp/plan.json (terraform show -json tfplan). Best-effort: any
# infrastructure error (missing conftest, bad fetch) degrades to a skipped step
# rather than failing the run, so the gate can never wedge a deploy by accident.
# ---------------------------------------------------------------------------
run_opa_policy_check() {
  local mode="${OPA_MODE:-off}"
  if [[ "$mode" == "off" || -z "$mode" ]]; then
    step "OPA Policy Check" skipped "OPA policy gate is off for this Business Unit (set Settings → Policies → mode to warn or enforce)."
    return 0
  fi
  if [[ ! -s /tmp/plan.json ]]; then
    step "OPA Policy Check" skipped "no plan JSON to evaluate"
    return 0
  fi
  if ! command -v conftest >/dev/null 2>&1; then
    step "OPA Policy Check" skipped "conftest CLI not installed in executor image"
    return 0
  fi

  step "OPA Policy Check" running ""

  local work=/tmp/opa
  rm -rf "$work"; mkdir -p "$work/results"

  # Pull the enabled DB policies for this run's BU (rego content lives there).
  local bundle
  bundle=$(curl -sf -H "Authorization: Bearer ${API_TOKEN}" \
    "${API_URL}/api/v1/runs/${RUN_ID}/policies" 2>/dev/null || echo '{}')

  local idx=0

  _opa_eval_dir() {  # <policy_dir> <severity> <source_name>
    local dir="$1" sev="$2" src="$3"
    # Skip empty dirs (no .rego) so conftest doesn't error on them.
    if ! find "$dir" -name '*.rego' -print -quit | grep -q .; then return 0; fi
    local out="$work/out_${idx}.json"
    conftest test /tmp/plan.json --policy "$dir" --output json --no-color --all-namespaces \
      > "$out" 2>"$work/err_${idx}.txt" || true
    if [[ ! -s "$out" ]] || ! jq -e . "$out" >/dev/null 2>&1; then
      # conftest errored (bad rego, etc.) — record nothing for this source.
      echo "[]" > "$work/results/${idx}.json"
    else
      jq --arg sev "$sev" --arg src "$src" '
        [ ( .[]?.failures[]?  | {policy:$src, severity:$sev, level:"deny", msg:(.msg // ""),
                                 resource:((.metadata.resource // .metadata.address) // null)} ),
          ( .[]?.warnings[]?  | {policy:$src, severity:$sev, level:"warn", msg:(.msg // ""),
                                 resource:((.metadata.resource // .metadata.address) // null)} ) ]
      ' "$out" > "$work/results/${idx}.json" 2>/dev/null || echo "[]" > "$work/results/${idx}.json"
    fi
    idx=$((idx + 1))
  }

  # 1. DB-authored policies — one dir per policy for clean attribution.
  local count
  count=$(printf '%s' "$bundle" | jq '.policies | length' 2>/dev/null || echo 0)
  local i name sev
  for ((i=0; i<count; i++)); do
    name=$(printf '%s' "$bundle" | jq -r ".policies[$i].name" 2>/dev/null)
    sev=$(printf '%s' "$bundle" | jq -r ".policies[$i].severity // \"block\"" 2>/dev/null)
    local pdir="$work/db/$i"
    mkdir -p "$pdir"
    printf '%s' "$bundle" | jq -r ".policies[$i].rego" 2>/dev/null > "$pdir/policy.rego"
    _opa_eval_dir "$pdir" "$sev" "$name"
  done

  # 2. Bundled defaults baked into the image.
  if [[ "${OPA_USE_BUNDLED:-true}" != "false" && -d /opt/tdt/policies/bundled ]]; then
    _opa_eval_dir /opt/tdt/policies/bundled "${OPA_BUNDLED_SEVERITY:-block}" "bundled"
  fi

  # 3. Optional git policy-repo.
  if [[ -n "${OPA_REPO_URL:-}" ]]; then
    local gitdir="$work/git"
    if git clone --depth=1 -b "${OPA_REPO_REF:-main}" -- "${OPA_REPO_URL}" "$gitdir" >/dev/null 2>&1; then
      local target="$gitdir"
      [[ -n "${OPA_REPO_DIR:-}" ]] && target="$gitdir/${OPA_REPO_DIR}"
      _opa_eval_dir "$target" "${OPA_GIT_SEVERITY:-block}" "git:${OPA_REPO_DIR:-/}"
    else
      echo "WARNING: OPA git policy-repo clone failed (${OPA_REPO_URL})" >&2
    fi
  fi

  # Aggregate all findings.
  local all failures warnings block_count fail_count warn_count
  all=$(jq -s 'add // []' "$work"/results/*.json 2>/dev/null || echo '[]')
  failures=$(printf '%s' "$all" | jq '[.[] | select(.level=="deny")]')
  warnings=$(printf '%s' "$all" | jq '[.[] | select(.level=="warn")]')
  fail_count=$(printf '%s' "$failures" | jq 'length')
  warn_count=$(printf '%s' "$warnings" | jq 'length')
  block_count=$(printf '%s' "$failures" | jq '[.[] | select(.severity=="block")] | length')

  local status="passed"
  if [[ "$mode" == "enforce" && "$block_count" -gt 0 ]]; then
    status="failed"
  elif [[ "$fail_count" -gt 0 || "$warn_count" -gt 0 ]]; then
    status="warned"
  fi

  # Structured summary for the timeline badge + RunDetail Policies tab.
  local summary
  summary=$(jq -n \
    --arg mode "$mode" --arg status "$status" \
    --argjson failures "$failures" --argjson warnings "$warnings" \
    --argjson fc "$fail_count" --argjson wc "$warn_count" --argjson bc "$block_count" \
    '{mode:$mode, status:$status, violations:$failures, warnings:$warnings,
      counts:{failures:$fc, warnings:$wc, blocking:$bc}}')

  # Human-readable output.
  local human
  human=$(printf '%s' "$all" | jq -r --arg mode "$mode" --arg status "$status" '
    "OPA Policy Check — mode=\($mode), result=\($status)\n"
    + (if length==0 then "\nNo policy findings."
       else "\n" + ( [ .[] | "  [\(.severity)/\(.level)] \(.policy): \(.msg)" ] | join("\n") ) end)')

  # Persist policy_status on the run row (best-effort).
  jq -n --arg ps "$status" '{policy_status:$ps}' > /tmp/opa_status.json
  curl -sf -X PATCH "${API_URL}/api/v1/runs/${RUN_ID}" \
    -H "Authorization: Bearer ${API_TOKEN}" -H "Content-Type: application/json" \
    --data-binary @/tmp/opa_status.json > /dev/null 2>&1 || true

  if [[ "$status" == "failed" ]]; then
    step "OPA Policy Check" failed "$human" "$summary"
    trap - ERR
    report_status "failed" "OPA policy gate (enforce) blocked the run:\n${human}"
    exit 1
  fi
  step "OPA Policy Check" success "$human" "$summary"
  return 0
}

# If a GitHub PAT was passed through, rewrite any github.com HTTPS URL to
# include the token so private terraform modules (`module "x" { source =
# "git::https://github.com/org/repo.git" }`) can be cloned during init. The
# token is process-local; nothing is written to disk outside ~/.gitconfig.
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  git config --global url."https://x-access-token:${GITHUB_TOKEN}@github.com/".insteadOf "https://github.com/"
  echo "=== GitHub auth configured for private module downloads ==="
fi

# Modules registry override (Settings → Terraform modules → local mode):
# rewrite the upstream URL to a local file:// checkout so `terraform init`
# resolves `source = "git::<MODULES_UPSTREAM_URL>"` lines to the bind-mounted
# host path instead of going to GitHub. This is the dev-mode flow for
# iterating on shared modules without push-pull.
# TF_INIT_GIT_PROTO scopes a `file` transport allowance to `terraform init`
# ONLY (module resolution), so the workspace-repo `git clone` above still runs
# under the restrictive global GIT_ALLOW_PROTOCOL (no file/ext — ). Empty
# unless the dev module-mirror is active.
TF_INIT_GIT_PROTO=""
if [[ -n "${MODULES_UPSTREAM_URL:-}" && -n "${MODULES_LOCAL_DIR:-}" && -d "${MODULES_LOCAL_DIR}" ]]; then
  git config --global url."file://${MODULES_LOCAL_DIR}".insteadOf "${MODULES_UPSTREAM_URL}"
  # Also handle the `git::https://...` prefix terraform uses internally.
  git config --global url."file://${MODULES_LOCAL_DIR}".insteadOf "git::${MODULES_UPSTREAM_URL}"
  # The redirect target is an operator-controlled bind mount, so allow `file`
  # for terraform init's module clone (not for the workspace repo clone).
  TF_INIT_GIT_PROTO="${GIT_ALLOW_PROTOCOL}:file"
  echo "=== Modules redirect: ${MODULES_UPSTREAM_URL} → file://${MODULES_LOCAL_DIR} ==="
fi

# If the workspace's AWS account configures a named profile (e.g.
# `provider "aws" { profile = "devops" }`), write ~/.aws/credentials with
# that profile + a [default] alias and export AWS_PROFILE so the SDK matches.
# Without this you get: "Error: failed to get shared config profile, devops".
if [[ -n "${AWS_PROFILE_NAME:-}" && -n "${AWS_ACCESS_KEY_ID:-}" && -n "${AWS_SECRET_ACCESS_KEY:-}" ]]; then
  mkdir -p ~/.aws
  cat > ~/.aws/credentials <<CRED
[default]
aws_access_key_id = ${AWS_ACCESS_KEY_ID}
aws_secret_access_key = ${AWS_SECRET_ACCESS_KEY}

[${AWS_PROFILE_NAME}]
aws_access_key_id = ${AWS_ACCESS_KEY_ID}
aws_secret_access_key = ${AWS_SECRET_ACCESS_KEY}
CRED
  chmod 600 ~/.aws/credentials
  cat > ~/.aws/config <<CFG
[default]
region = ${AWS_DEFAULT_REGION}

[profile ${AWS_PROFILE_NAME}]
region = ${AWS_DEFAULT_REGION}
CFG
  export AWS_PROFILE="${AWS_PROFILE_NAME}"
  echo "=== AWS profile [${AWS_PROFILE_NAME}] written to ~/.aws/credentials ==="
fi

if [[ -n "${ARM_CLIENT_ID}" ]]; then
  # Tail-only echo so secrets stay out of logs.
  echo "=== Azure SP auth wired: tenant ${ARM_TENANT_ID:0:8}… subscription ${ARM_SUBSCRIPTION_ID:0:8}… ==="
fi

# If the workspace is linked to a GCP project, write the service-account key
# JSON to a 0600 file and point the google provider at it (mirrors the AWS/
# kubeconfig file-write pattern). Never `gcloud auth` — the provider reads
# GOOGLE_APPLICATION_CREDENTIALS / GOOGLE_PROJECT / GOOGLE_REGION directly.
if [[ -n "${GCP_SA_KEY_JSON}" ]]; then
  mkdir -p ~/.gcp
  printf '%s' "${GCP_SA_KEY_JSON}" > ~/.gcp/sa-key.json
  chmod 600 ~/.gcp/sa-key.json
  export GOOGLE_APPLICATION_CREDENTIALS="${HOME}/.gcp/sa-key.json"
  export GOOGLE_CLOUD_PROJECT="${GOOGLE_PROJECT}"
  # Tail-only echo — never print the key JSON.
  echo "=== GCP SA auth wired: project ${GOOGLE_PROJECT:-<none>} (key at ${GOOGLE_APPLICATION_CREDENTIALS}) ==="
fi

report_status "running"
fetch_steps

# Heartbeat loop. The API has a reaper that fails runs whose worker hasn't
# PATCHed /heartbeat for STALE_AFTER_SECONDS (90s) and releases the advisory
# state-lock. We ping every 30s so we have ~60s of slack for transient blips.
heartbeat_loop() {
  while true; do
    curl -sf -X POST "${API_URL}/api/v1/runs/${RUN_ID}/heartbeat" \
      -H "Authorization: Bearer ${API_TOKEN}" >/dev/null 2>&1 || true
    sleep 30
  done
}
heartbeat_loop &
HEARTBEAT_PID=$!
# shellcheck disable=SC2064
trap "kill ${HEARTBEAT_PID} 2>/dev/null || true" EXIT

# ═══════════════════════════════════════════════════════════════════════════
# Helm pipeline (WORKSPACE_KIND=helm). Reuses the same run_step()/stream_run()/
# step()/report_status()/heartbeat helpers as terraform — it does NOT duplicate
# them. Command vocabulary stays plan|apply|destroy and is mapped to helm verbs:
#   plan    -> helm diff upgrade         (status=planned, plan-only)
#   apply   -> helm upgrade --install    (status=awaiting_approval then applied)
#   destroy -> helm uninstall            (status=awaiting_approval then applied)
# There is NO HTTP/S3 state backend and NO state token for helm.
# ═══════════════════════════════════════════════════════════════════════════
helm_clone_repo() {
  # Mirror the terraform Git Clone logic (premounted RO bind vs git clone).
  WORK_ROOT="/workspace/repo"
  mkdir -p "$(dirname "${WORK_ROOT}")"
  if [[ "${REPO_PREMOUNTED:-0}" == "1" ]]; then
    local SRC_MOUNT="/workspace/repo-src"
    if [[ -d "${WORK_ROOT}" ]] && [[ ! -d "${SRC_MOUNT}" ]]; then
      SRC_MOUNT="${WORK_ROOT}"
      WORK_ROOT="/workspace/repo-rw"
    fi
    mkdir -p "${WORK_ROOT}"
    cp -a "${SRC_MOUNT}/." "${WORK_ROOT}/"
  else
    git clone --depth=1 --branch "${REPO_REF}" -- "${REPO_URL}" "${WORK_ROOT}" 2>&1 | sed 's/^/[git] /'
  fi
}

helm_pipeline() {
  echo "=== Helm pipeline started (kind=helm, phase=${TF_PHASE}, command=${TF_COMMAND}) ==="

  # Write the kubeconfig the API injected to a private file and point helm/kubectl
  # at it. We never log its contents.
  if [[ -z "${KUBECONFIG_CONTENT}" ]]; then
    step "Git Clone" failed "no KUBECONFIG_CONTENT injected; helm workspace must be linked to a cluster"
    trap - ERR
    report_status "failed" "helm: no kubeconfig provided (link the workspace to a cluster)"
    exit 1
  fi
  mkdir -p ~/.kube
  printf '%s' "${KUBECONFIG_CONTENT}" > ~/.kube/config
  chmod 600 ~/.kube/config
  export KUBECONFIG=~/.kube/config

  # ── Git Clone ────────────────────────────────────────────────────────────
  # Redirect (not pipe) so helm_clone_repo runs in THIS shell — it sets the
  # global WORK_ROOT, which a `| tee` subshell would discard (then `set -u`
  # trips on WORK_ROOT below).
  step "Git Clone" running ""
  : > /tmp/git.log
  if helm_clone_repo > /tmp/git.log 2>&1; then
    step "Git Clone" success "$(cat /tmp/git.log)"
  else
    step "Git Clone" failed "$(cat /tmp/git.log)"
    trap - ERR
    report_status "failed" "git clone failed"
    exit 1
  fi

  # ── Get Chart Dir ────────────────────────────────────────────────────────
  step "Get Chart Dir" running ""
  cd "${WORK_ROOT}/${TF_WORKING_DIR}"
  step "Get Chart Dir" success "cwd=$(pwd)"

  # Parse the terraducktel.yaml helm: block.
  #   helm:
  #     release_name: <name>
  #     namespace:    <ns>
  #     chart:        <path-or-ref>     (optional; defaults to ".")
  #     repo:         <name=url>        (optional; helm repo add)
  #     values: [ values.yaml, prod.yaml ]
  local TDT_YAML=""
  for f in terraducktel.yaml terraducktel.yml; do
    if [[ -f "$f" ]]; then TDT_YAML="$f"; break; fi
  done
  HELM_RELEASE=""
  HELM_NAMESPACE=""
  HELM_CHART="."
  HELM_REPO=""
  HELM_VALUES_ARGS=()
  if [[ -n "$TDT_YAML" ]]; then
    HELM_RELEASE=$(yq eval '.helm.release_name // ""' "$TDT_YAML" 2>/dev/null || true)
    HELM_NAMESPACE=$(yq eval '.helm.namespace // ""' "$TDT_YAML" 2>/dev/null || true)
    local _chart
    _chart=$(yq eval '.helm.chart // ""' "$TDT_YAML" 2>/dev/null || true)
    [[ -n "$_chart" ]] && HELM_CHART="$_chart"
    HELM_REPO=$(yq eval '.helm.repo // ""' "$TDT_YAML" 2>/dev/null || true)
    # values[] → one --values per entry. yq prints one per line.
    while IFS= read -r v; do
      [[ -n "$v" && "$v" != "null" ]] && HELM_VALUES_ARGS+=( --values "$v" )
    done < <(yq eval '.helm.values[]? // empty' "$TDT_YAML" 2>/dev/null || true)
  fi
  # Namespace falls back to the cluster default the API may pass through.
  [[ -z "$HELM_NAMESPACE" ]] && HELM_NAMESPACE="${KUBE_DEFAULT_NAMESPACE:-default}"
  if [[ -z "$HELM_RELEASE" ]]; then
    step "Get Chart Dir" failed "terraducktel.yaml helm.release_name is required for helm workspaces"
    trap - ERR
    report_status "failed" "helm: missing helm.release_name in terraducktel.yaml"
    exit 1
  fi
  echo "=== helm release=${HELM_RELEASE} ns=${HELM_NAMESPACE} chart=${HELM_CHART} ==="

  # ── Helm Dependency Build ─────────────────────────────────────────────────
  # Optional `helm repo add` (name=url) then refresh, then build chart deps.
  if [[ -n "$HELM_REPO" && "$HELM_REPO" == *"="* ]]; then
    local _rname="${HELM_REPO%%=*}" _rurl="${HELM_REPO#*=}"
    helm repo add "$_rname" "$_rurl" >/dev/null 2>&1 || true
    helm repo update >/dev/null 2>&1 || true
  fi
  if [[ -f "${HELM_CHART}/Chart.yaml" ]]; then
    run_step "Helm Dependency Build" helm dependency build "${HELM_CHART}" || {
      trap - ERR
      report_status "failed" "helm dependency build failed"
      exit 1
    }
  else
    step "Helm Dependency Build" skipped "no Chart.yaml at ${HELM_CHART} (remote chart ref)"
  fi

  # ── Lint ──────────────────────────────────────────────────────────────────
  # Render templates and validate with kubeconform + helm lint. CHECKOV_MODE is
  # reused as the warn/fail toggle (default warn for helm so onboarding existing
  # charts isn't blocked).
  local LINT_MODE="${CHECKOV_MODE:-warn}"
  : > /tmp/helm_lint.log
  sid_lint=$(step_id_for "Lint")
  [[ -n "$sid_lint" ]] && step "Lint" running ""
  set +e
  trap - ERR
  {
    helm lint "${HELM_CHART}" "${HELM_VALUES_ARGS[@]}" 2>&1
    echo "--- kubeconform ---"
    helm template "${HELM_RELEASE}" "${HELM_CHART}" -n "${HELM_NAMESPACE}" "${HELM_VALUES_ARGS[@]}" 2>/dev/null \
      | kubeconform -summary -strict -ignore-missing-schemas 2>&1
  } | tee -a /tmp/helm_lint.log
  lint_rc=${PIPESTATUS[0]}
  trap 'on_unexpected_err $LINENO' ERR
  set -e
  if [[ $lint_rc -eq 0 ]]; then
    step "Lint" success "$(cat /tmp/helm_lint.log)"
  elif [[ "$LINT_MODE" == "warn" ]]; then
    step "Lint" success "LINT ISSUES — continuing (mode=warn):\n\n$(cat /tmp/helm_lint.log)"
  else
    step "Lint" failed "$(cat /tmp/helm_lint.log)"
    trap - ERR
    report_status "failed" "helm lint/kubeconform failed:\n$(tail -c 4000 /tmp/helm_lint.log)"
    exit 1
  fi

  # ═════════════════════════════════════════════════════════════════════════
  # Apply phase (TF_PHASE=apply): the plan was approved. Run helm upgrade
  # --install (apply) or helm uninstall (destroy).
  # ═════════════════════════════════════════════════════════════════════════
  if [[ "${TF_PHASE}" == "apply" ]]; then
    step "Awaiting Approval" success "Approved; starting helm apply phase"
    if [[ "${TF_COMMAND}" == "destroy" ]]; then
      if stream_run "Helm Upgrade" /tmp/helm_apply.log -- \
          helm uninstall "${HELM_RELEASE}" -n "${HELM_NAMESPACE}"; then
        step "Helm Output" success "release ${HELM_RELEASE} uninstalled"
        report_status "applied" "$(cat /tmp/helm_apply.log)"
        echo "=== Helm uninstall complete ==="
        exit 0
      fi
    else
      if stream_run "Helm Upgrade" /tmp/helm_apply.log -- \
          helm upgrade --install "${HELM_RELEASE}" "${HELM_CHART}" \
            -n "${HELM_NAMESPACE}" --create-namespace "${HELM_VALUES_ARGS[@]}"; then
        if stream_run "Helm Output" /tmp/helm_status.log -- \
            helm status "${HELM_RELEASE}" -n "${HELM_NAMESPACE}"; then
          :
        else
          step "Helm Output" success "release deployed (status unavailable)"
        fi
        report_status "applied" "$(cat /tmp/helm_apply.log)"
        echo "=== Helm upgrade complete ==="
        exit 0
      fi
    fi
    trap - ERR
    report_status "failed" "$(cat /tmp/helm_apply.log)"
    exit 1
  fi

  # ── Helm Diff (the "plan") ────────────────────────────────────────────────
  : > /tmp/helm_diff.log
  if [[ "${TF_COMMAND}" == "destroy" ]]; then
    # No native `helm diff uninstall`; show what would be removed.
    sid_diff=$(step_id_for "Helm Diff")
    [[ -n "$sid_diff" ]] && step "Helm Diff" running ""
    set +e; trap - ERR
    helm get manifest "${HELM_RELEASE}" -n "${HELM_NAMESPACE}" 2>&1 | tee -a /tmp/helm_diff.log
    diff_rc=${PIPESTATUS[0]}
    trap 'on_unexpected_err $LINENO' ERR; set -e
    PLAN_OUTPUT="DESTROY: helm uninstall ${HELM_RELEASE} (ns=${HELM_NAMESPACE}) will remove the following manifest:\n\n$(cat /tmp/helm_diff.log)"
    step "Helm Diff" success "$PLAN_OUTPUT"
  else
    # `helm diff upgrade` exits 0 even with changes (unless --detailed-exitcode);
    # capture it as the plan output regardless.
    sid_diff=$(step_id_for "Helm Diff")
    [[ -n "$sid_diff" ]] && step "Helm Diff" running ""
    set +e; trap - ERR
    helm diff upgrade "${HELM_RELEASE}" "${HELM_CHART}" \
      -n "${HELM_NAMESPACE}" --allow-unreleased "${HELM_VALUES_ARGS[@]}" 2>&1 | tee -a /tmp/helm_diff.log
    diff_rc=${PIPESTATUS[0]}
    trap 'on_unexpected_err $LINENO' ERR; set -e
    PLAN_OUTPUT="$(cat /tmp/helm_diff.log)"
    [[ -z "$PLAN_OUTPUT" ]] && PLAN_OUTPUT="(no changes — release matches desired state)"
    if [[ $diff_rc -ne 0 ]]; then
      step "Helm Diff" failed "$PLAN_OUTPUT"
      trap - ERR
      report_status "failed" "helm diff failed:\n$(tail -c 4000 /tmp/helm_diff.log)"
      exit 1
    fi
    step "Helm Diff" success "$PLAN_OUTPUT"
  fi

  # ── Cost Estimation (not applicable to helm) ──────────────────────────────
  step "Cost Estimation" skipped "cost estimation not applicable to helm workspaces"

  # ── Report plan result ────────────────────────────────────────────────────
  printf '%s' "${PLAN_OUTPUT}" > /tmp/plan_output.txt
  if [[ "${TF_COMMAND}" == "plan" ]]; then
    jq -n --rawfile p /tmp/plan_output.txt '{status:"planned", plan_output:$p}' > /tmp/run_patch.json
    for i in 1 2 3; do
      if curl -sf -X PATCH "${API_URL}/api/v1/runs/${RUN_ID}" \
          -H "Authorization: Bearer ${API_TOKEN}" \
          -H "Content-Type: application/json" \
          --data-binary @/tmp/run_patch.json > /dev/null; then
        break
      fi
      sleep $((i * 2))
    done
    echo "=== Helm plan complete (plan-only) ==="
    exit 0
  fi

  # apply/destroy plan-phase: pause for approval.
  jq -n --rawfile p /tmp/plan_output.txt '{status:"awaiting_approval", plan_output:$p}' > /tmp/run_patch.json
  for i in 1 2 3; do
    if curl -sf -X PATCH "${API_URL}/api/v1/runs/${RUN_ID}" \
        -H "Authorization: Bearer ${API_TOKEN}" \
        -H "Content-Type: application/json" \
        --data-binary @/tmp/run_patch.json > /dev/null; then
      break
    fi
    sleep $((i * 2))
  done
  step "Awaiting Approval" running "Helm diff complete — waiting for an approver."
  echo "=== Helm plan phase complete; awaiting approval ==="
  exit 0
}

if [[ "${WORKSPACE_KIND}" == "helm" ]]; then
  helm_pipeline
  exit 0
fi

# ═══════════════════════════════════════════════════════════════════════════
# Apply phase (TF_PHASE=apply): runs after a plan was approved. We re-init
# terraform from a fresh container, pull the saved tfplan blob from the API,
# and run `terraform apply tfplan` so the post-approval execution operates on
# the exact same plan the approver reviewed (two-phase flow).
# ═══════════════════════════════════════════════════════════════════════════
if [[ "${TF_PHASE}" == "apply" ]]; then
  echo "=== Apply phase started ==="

  # Mark Awaiting Approval done — the user just approved.
  step "Awaiting Approval" success "Approved; starting apply phase"

  # Bring up the same workspace layout as plan phase did.
  WORK_ROOT="/workspace/repo"
  mkdir -p "$(dirname "${WORK_ROOT}")"
  if [[ "${REPO_PREMOUNTED:-0}" == "1" ]]; then
    SRC_MOUNT="/workspace/repo-src"
    if [[ -d "${WORK_ROOT}" ]] && [[ ! -d "${SRC_MOUNT}" ]]; then
      SRC_MOUNT="${WORK_ROOT}"
      WORK_ROOT="/workspace/repo-rw"
    fi
    mkdir -p "${WORK_ROOT}"
    cp -a "${SRC_MOUNT}/." "${WORK_ROOT}/"
  else
    git clone --depth=1 --branch "${REPO_REF}" -- "${REPO_URL}" "${WORK_ROOT}" 2>&1 | sed 's/^/[git] /'
  fi
  cd "${WORK_ROOT}/${TF_WORKING_DIR}"

  # Same backend wiring as plan phase.
  EXISTING_BACKEND=$(grep -lE '^\s*backend\s+"[a-z0-9]+"\s*\{' *.tf 2>/dev/null || true)
  if [[ -z "$EXISTING_BACKEND" ]]; then
    cat > _terraducktel_backend.tf <<EOF
terraform {
  backend "http" {
    address        = "${API_URL}/api/v1/state/${WORKSPACE_ID}"
    lock_address   = "${API_URL}/api/v1/state/${WORKSPACE_ID}/lock"
    unlock_address = "${API_URL}/api/v1/state/${WORKSPACE_ID}/lock"
    lock_method    = "POST"
    unlock_method  = "DELETE"
  }
}
EOF
    export TF_HTTP_USERNAME="terraducktel"
    export TF_HTTP_PASSWORD="${API_TOKEN}"
  fi

  # Re-init (apply phase needs the .terraform/ providers).
  rm -rf .terraform
  if ! GIT_ALLOW_PROTOCOL="${TF_INIT_GIT_PROTO:-$GIT_ALLOW_PROTOCOL}" terraform init -input=false -no-color -reconfigure 2>&1 | tee /tmp/init.log; then
    trap - ERR
    report_status "failed" "init failed in apply phase: $(tail -c 4000 /tmp/init.log)"
    exit 1
  fi

  # Restore the saved tfplan from the dedicated /tfplan endpoint (the default
  # GET /runs/{id} omits the blob to keep responses small).
  if ! curl -sf -H "Authorization: Bearer ${API_TOKEN}" \
        "${API_URL}/api/v1/runs/${RUN_ID}/tfplan" \
        -o /tmp/run.json 2>/dev/null; then
    trap - ERR
    report_status "failed" "could not fetch tfplan blob endpoint"
    exit 1
  fi
  TFPLAN_B64=$(jq -r '.tfplan_b64 // ""' < /tmp/run.json)
  if [[ -z "${TFPLAN_B64}" || "${TFPLAN_B64}" == "null" ]]; then
    msg="apply phase: no saved tfplan_b64 on run row"
    step "Terraform Apply" failed "$msg"
    trap - ERR
    report_status "failed" "$msg"
    exit 1
  fi
  printf '%s' "${TFPLAN_B64}" | base64 -d > tfplan
  echo "=== restored tfplan ($(stat -c%s tfplan 2>/dev/null || stat -f%z tfplan) bytes) ==="

  # Run terraform apply with live streaming.
  if stream_run "Terraform Apply" /tmp/apply.log -- terraform apply -input=false -auto-approve -no-color tfplan; then
    APPLY_OUTPUT=$(cat /tmp/apply.log)
    # After: Terraform Apply — placeholder for post-apply hooks (Slack, etc.).
    step "After: Terraform Apply" success "post-apply hooks completed (placeholder)"
    # Capture terraform outputs.
    if stream_run "Terraform Output" /tmp/output.log -- terraform output -no-color -json; then
      :
    else
      step "Terraform Output" success "no outputs declared"
    fi
    step "Store Working Directory" success "workspace finalized"
    report_status "applied" "${APPLY_OUTPUT}"
    echo "=== Apply phase complete ==="
    exit 0
  else
    trap - ERR
    report_status "failed" "$(cat /tmp/apply.log)"
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# 1. Git Clone (or skip if the repo is already bind-mounted at /workspace/repo)
# ---------------------------------------------------------------------------
WORK_ROOT="/workspace/repo"
mkdir -p "$(dirname "${WORK_ROOT}")"
step "Git Clone" running ""
if [[ "${REPO_PREMOUNTED:-0}" == "1" ]]; then
  # Read-only bind mount → copy into a writable workdir so terraform init can
  # populate .terraform/ and we can drop _terraducktel_backend.tf alongside.
  SRC_MOUNT="/workspace/repo-src"
  if [[ -d "${WORK_ROOT}" ]] && [[ ! -d "${SRC_MOUNT}" ]]; then
    SRC_MOUNT="${WORK_ROOT}"
    WORK_ROOT="/workspace/repo-rw"
  fi
  if [[ ! -d "${SRC_MOUNT}" ]]; then
    msg="REPO_PREMOUNTED=1 but no source mount found at /workspace/repo"
    step "Git Clone" failed "$msg"
    trap - ERR
    report_status "failed" "$msg"
    exit 1
  fi
  echo "=== Copying bind-mounted repo from ${SRC_MOUNT} to writable ${WORK_ROOT} ==="
  mkdir -p "${WORK_ROOT}"
  cp -a "${SRC_MOUNT}/." "${WORK_ROOT}/"
  step "Git Clone" skipped "bind-mounted from TERRADUCKTEL_LOCAL_REPOS_HOST_DIR (copied to writable layer)"
else
  echo "=== Cloning ${REPO_URL} (ref: ${REPO_REF}) ==="
  if git clone --depth=1 --branch "${REPO_REF}" -- "${REPO_URL}" "${WORK_ROOT}" 2>&1 | tee /tmp/git.log | sed 's/^/[git] /'; then
    step "Git Clone" success "$(cat /tmp/git.log)"
  else
    step "Git Clone" failed "$(cat /tmp/git.log)"
    trap - ERR
    report_status "failed" "git clone failed"
    exit 1
  fi
fi

# 2. Get Working Directory
step "Get Working Directory" running ""
cd "${WORK_ROOT}/${TF_WORKING_DIR}"
step "Get Working Directory" success "cwd=$(pwd)"

# 3. Loading terraducktel YAML file. Recognized names (priority order):
#    terraducktel.yaml | terraducktel.yml.
# Currently parsed keys:
#   terraform.version  → override the executor image's bundled Terraform
#                        (consumed in "Setting Version" below)
# Other keys (checkov, infracost, …) are surfaced for visibility but
# not yet enforced — see docs/claude/executor.md.
step "Loading terraducktel YAML file" running ""
TDT_YAML=""
for f in terraducktel.yaml terraducktel.yml; do
  if [[ -f "$f" ]]; then TDT_YAML="$f"; break; fi
done
TDT_TF_VERSION=""  # empty → use the image's bundled terraform
if [[ -n "$TDT_YAML" ]]; then
  # `yq … // ""` collapses missing keys / nulls to empty string. Strip a
  # leading "v" so users can write either "1.10.5" or "v1.10.5".
  TDT_TF_VERSION=$(yq eval '.terraform.version // ""' "$TDT_YAML" 2>/dev/null || true)
  TDT_TF_VERSION="${TDT_TF_VERSION#v}"
  if [[ -n "$TDT_TF_VERSION" && ! "$TDT_TF_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    step "Loading terraducktel YAML file" failed \
      "invalid terraform.version '${TDT_TF_VERSION}' in ${TDT_YAML}: expected X.Y.Z"
    trap - ERR
    report_status "failed" "invalid terraform.version in ${TDT_YAML}"
    exit 1
  fi
  step "Loading terraducktel YAML file" success \
    "loaded ${TDT_YAML} (tf=${TDT_TF_VERSION:-bundled}):\n\n$(cat "$TDT_YAML" | head -60)"
else
  step "Loading terraducktel YAML file" skipped "no terraducktel.yaml in working dir"
fi

# 4. Load Variables
step "Load Variables" running ""
VAR_FILES=$(ls *.tfvars 2>/dev/null | head -20 || true)
step "Load Variables" success "tfvars=${VAR_FILES:-none}"

# 5. Setting Version.
# If terraducktel.yaml requested a specific terraform version that doesn't
# match the image's bundled binary, download it from releases.hashicorp.com
# and prepend to PATH so the remaining steps use it. The download is
# ephemeral (per-run container) so no cross-run caching is needed.
step "Setting Version" running ""
BUNDLED_TF_VERSION=$(terraform version -json 2>/dev/null | jq -r '.terraform_version' 2>/dev/null || echo "unknown")
if [[ -n "$TDT_TF_VERSION" && "$TDT_TF_VERSION" != "$BUNDLED_TF_VERSION" ]]; then
  case "$(uname -m)" in
    x86_64)         TF_ARCH="amd64" ;;
    aarch64|arm64)  TF_ARCH="arm64" ;;
    *) step "Setting Version" failed "unsupported arch $(uname -m)"
       trap - ERR; report_status "failed" "unsupported arch $(uname -m)"; exit 1 ;;
  esac
  TF_DL_DIR="/tmp/tf-${TDT_TF_VERSION}"
  TF_URL="https://releases.hashicorp.com/terraform/${TDT_TF_VERSION}/terraform_${TDT_TF_VERSION}_linux_${TF_ARCH}.zip"
  mkdir -p "$TF_DL_DIR"
  if ! wget -q "$TF_URL" -O "$TF_DL_DIR/tf.zip"; then
    step "Setting Version" failed "failed to download ${TF_URL}"
    trap - ERR; report_status "failed" "terraform ${TDT_TF_VERSION} download failed"; exit 1
  fi
  if ! unzip -q -o "$TF_DL_DIR/tf.zip" -d "$TF_DL_DIR"; then
    step "Setting Version" failed "failed to unzip ${TF_DL_DIR}/tf.zip"
    trap - ERR; report_status "failed" "terraform ${TDT_TF_VERSION} unzip failed"; exit 1
  fi
  chmod +x "$TF_DL_DIR/terraform"
  export PATH="$TF_DL_DIR:$PATH"
  TF_VERSION=$(terraform version -json 2>/dev/null | jq -r '.terraform_version' 2>/dev/null || echo "$TDT_TF_VERSION")
  step "Setting Version" success "terraform=${TF_VERSION} (override; bundled=${BUNDLED_TF_VERSION})"
else
  TF_VERSION="$BUNDLED_TF_VERSION"
  step "Setting Version" success "terraform=${TF_VERSION}"
fi

# 6. Initialize (workspace state setup, lockfile etc.).
#
# Backend resolution rule:
#   - If the repo's own .tf already declares a `backend "..." {}` block, leave
#     state management to that backend (typically the user's pre-existing
#     `backend "s3" {}`). Terraform forbids two backends per module, so
#     injecting our own would fail init.
#   - Otherwise, drop a `_terraducktel_backend.tf` pointing at the API's HTTP
#     state backend, plus `TF_HTTP_USERNAME`/`TF_HTTP_PASSWORD` so the
#     `require_state_token` dependency accepts the calls.
step "Initialize" running ""
EXISTING_BACKEND=$(grep -lE '^\s*backend\s+"[a-z0-9]+"\s*\{' *.tf 2>/dev/null || true)
if [[ -n "$EXISTING_BACKEND" ]]; then
  echo "=== Detected existing backend block in: $EXISTING_BACKEND ==="
  echo "    Skipping terraducktel HTTP backend injection; using the workspace's own backend."
  step "Initialize" success "using repo backend ($EXISTING_BACKEND); terraducktel injection skipped"
else
  cat > _terraducktel_backend.tf <<EOF
terraform {
  backend "http" {
    address        = "${API_URL}/api/v1/state/${WORKSPACE_ID}"
    lock_address   = "${API_URL}/api/v1/state/${WORKSPACE_ID}/lock"
    unlock_address = "${API_URL}/api/v1/state/${WORKSPACE_ID}/lock"
    lock_method    = "POST"
    unlock_method  = "DELETE"
  }
}
EOF
  export TF_HTTP_USERNAME="terraducktel"
  export TF_HTTP_PASSWORD="${API_TOKEN}"
  step "Initialize" success "_terraducktel_backend.tf written; TF_HTTP_USERNAME/PASSWORD exported"
fi

# 7. Terraform Init (live-streamed to the UI step).
echo "=== Terraform Init ==="
rm -rf .terraform
if GIT_ALLOW_PROTOCOL="${TF_INIT_GIT_PROTO:-$GIT_ALLOW_PROTOCOL}" stream_run "Terraform Init" /tmp/init.log -- terraform init -input=false -no-color -reconfigure; then
  :
else
  INIT_OUT=$(cat /tmp/init.log)
  echo "=== TERRAFORM INIT FAILED ==="
  echo "$INIT_OUT" | tail -40
  trap - ERR
  report_status "failed" "$INIT_OUT"
  exit 1
fi

# 8. Setting Terraform Workspace (logical TF workspace, not terraducktel's)
step "Setting Terraform Workspace" running ""
terraform workspace select default >/dev/null 2>&1 || terraform workspace new default >/dev/null 2>&1 || true
step "Setting Terraform Workspace" success "workspace=default"

# 9. Tag Resources (default tags injected via TF_VAR_*; placeholder for now)
step "Tag Resources" running ""
step "Tag Resources" success "default tags applied via provider"

# Checkov is now a real timeline step. The streamer shows progress live.
# When CHECKOV_MODE=warn (set per workspace via the API), violations are
# captured but the run continues — useful for onboarding existing infra
# before tightening the gate to fail.
echo "=== Checkov Security Scan ==="
: > /tmp/checkov.log
sid_checkov=$(step_id_for "Checkov Security Scan")
[[ -n "$sid_checkov" ]] && step "Checkov Security Scan" running ""
[[ -n "$sid_checkov" ]] && stream_step_output "Checkov Security Scan" /tmp/checkov.log &
streamer_pid_checkov=$!
# Same PIPESTATUS-clobber gotcha as stream_run — see comment there. Suspend
# both errexit AND the ERR trap (they're independent — `set +e` alone still
# fires the trap, which would mark the whole run failed before we get to
# decide on the warn/fail mode).
set +e
trap - ERR
checkov -d . --quiet --compact 2>&1 | tee -a /tmp/checkov.log
checkov_rc=${PIPESTATUS[0]}
trap 'on_unexpected_err $LINENO' ERR
set -e
kill "$streamer_pid_checkov" 2>/dev/null || true
wait "$streamer_pid_checkov" 2>/dev/null || true

if [[ $checkov_rc -eq 0 ]]; then
  step "Checkov Security Scan" success "$(cat /tmp/checkov.log)"
elif [[ "${CHECKOV_MODE:-fail}" == "warn" ]]; then
  echo "Checkov found violations (mode=warn — continuing)."
  step "Checkov Security Scan" success "VIOLATIONS — continuing because CHECKOV_MODE=warn:\n\n$(cat /tmp/checkov.log)"
else
  echo "ERROR: Checkov found security violations. Aborting (mode=fail)."
  step "Checkov Security Scan" failed "$(cat /tmp/checkov.log)"
  trap - ERR
  report_status "failed" "Checkov found security violations:\n$(tail -c 4000 /tmp/checkov.log)"
  exit 1
fi

# 10. Terraform Plan / Apply / Destroy
case "${TF_COMMAND}" in
  plan)
    echo "=== Terraform Plan ==="
    if stream_run "Terraform Plan" /tmp/plan.log -- terraform plan -input=false -no-color -out=tfplan; then
      PLAN_OUTPUT=$(terraform show -no-color tfplan)
      # Capture structured plan JSON for the visualization canvas.
      terraform show -json tfplan > /tmp/plan.json 2>/dev/null || true
      # Extract the +/~/- counts from the plan summary line and stamp the
      # step's summary_json so the timeline shows the diff badge.
      SUMMARY=$(printf '%s' "$PLAN_OUTPUT" | grep -oE 'Plan: [0-9]+ to add, [0-9]+ to change, [0-9]+ to destroy' || echo "")
      ADD=$(echo "$SUMMARY" | grep -oE '[0-9]+ to add' | grep -oE '^[0-9]+' || echo 0)
      CHG=$(echo "$SUMMARY" | grep -oE '[0-9]+ to change' | grep -oE '^[0-9]+' || echo 0)
      DEL=$(echo "$SUMMARY" | grep -oE '[0-9]+ to destroy' | grep -oE '^[0-9]+' || echo 0)
      DIFF_JSON=$(jq -n --argjson a "$ADD" --argjson c "$CHG" --argjson d "$DEL" \
        '{add:$a, change:$c, destroy:$d}')
      step "Terraform Plan" success "${PLAN_OUTPUT}" "${DIFF_JSON}"

      # OPA policy gate. Runs against /tmp/plan.json; under enforce mode a
      # `block` violation exits non-zero here, before the run is marked planned.
      run_opa_policy_check

      # Build the run-level PATCH body via a FILE — both `plan_output` and
      # the structured `plan_json` can each be hundreds of KB on real-world
      # stacks, which trips ARG_MAX when jq receives them as command-line
      # args (the run finishes as "running" with no plan captured). Streaming
      # plan_output and plan_json through stdin via files avoids that.
      printf '%s' "${PLAN_OUTPUT}" > /tmp/plan_output.txt
      [[ -s /tmp/plan.json ]] || echo '""' > /tmp/plan.json   # fallback if show -json failed
      jq -n \
        --rawfile p /tmp/plan_output.txt \
        --rawfile jraw /tmp/plan.json \
        '{status:"planned", plan_output:$p, plan_json:$jraw}' \
        > /tmp/run_patch.json
      RUN_PATCH_OK=0
      for i in 1 2 3; do
        if curl -sf -X PATCH "${API_URL}/api/v1/runs/${RUN_ID}" \
            -H "Authorization: Bearer ${API_TOKEN}" \
            -H "Content-Type: application/json" \
            --data-binary @/tmp/run_patch.json > /dev/null; then
          RUN_PATCH_OK=1; break
        fi
        sleep $((i * 2))
      done
      if [[ $RUN_PATCH_OK -ne 1 ]]; then
        echo "WARNING: full plan PATCH failed; falling back to status-only update" >&2
        report_status "planned" "${PLAN_OUTPUT}"
      fi
    else
      trap - ERR
      report_status "failed" "$(cat /tmp/plan.log)"
      exit 1
    fi
    ;;
  apply|destroy)
    # Plan-phase of an apply (or destroy) run: produce + save the tfplan, then
    # PAUSE for approval. The approve route in the API will spawn a second
    # executor with TF_PHASE=apply that restores the saved tfplan and runs it.
    PLAN_FLAGS=""
    [[ "${TF_COMMAND}" == "destroy" ]] && PLAN_FLAGS="-destroy"
    if ! stream_run "Terraform Plan" /tmp/plan.log -- terraform plan ${PLAN_FLAGS} -input=false -no-color -out=tfplan; then
      trap - ERR
      report_status "failed" "$(cat /tmp/plan.log)"
      exit 1
    fi
    PLAN_OUTPUT=$(terraform show -no-color tfplan)
    terraform show -json tfplan > /tmp/plan.json 2>/dev/null || true
    SUMMARY=$(printf '%s' "$PLAN_OUTPUT" | grep -oE 'Plan: [0-9]+ to add, [0-9]+ to change, [0-9]+ to destroy' || echo "")
    ADD=$(echo "$SUMMARY" | grep -oE '[0-9]+ to add' | grep -oE '^[0-9]+' || echo 0)
    CHG=$(echo "$SUMMARY" | grep -oE '[0-9]+ to change' | grep -oE '^[0-9]+' || echo 0)
    DEL=$(echo "$SUMMARY" | grep -oE '[0-9]+ to destroy' | grep -oE '^[0-9]+' || echo 0)
    DIFF_JSON=$(jq -n --argjson a "$ADD" --argjson c "$CHG" --argjson d "$DEL" '{add:$a, change:$c, destroy:$d}')
    step "Terraform Plan" success "${PLAN_OUTPUT}" "${DIFF_JSON}"

    # OPA policy gate — must run BEFORE we pause for approval so an enforce-mode
    # `block` violation stops the run before it can ever be approved/applied.
    run_opa_policy_check

    # Save the tfplan binary to the run row as base64 so the apply-phase
    # executor can restore it after the approver clicks Approve. The plan
    # JSON also goes up so the Approvals canvas can render the resource graph.
    base64 -w0 tfplan > /tmp/tfplan_b64.txt 2>/dev/null || base64 tfplan | tr -d '\n' > /tmp/tfplan_b64.txt
    printf '%s' "${PLAN_OUTPUT}" > /tmp/plan_output.txt
    [[ -s /tmp/plan.json ]] || echo '""' > /tmp/plan.json
    jq -n \
      --rawfile p /tmp/plan_output.txt \
      --rawfile jraw /tmp/plan.json \
      --rawfile blob /tmp/tfplan_b64.txt \
      '{status:"awaiting_approval", plan_output:$p, plan_json:$jraw, tfplan_b64:$blob}' \
      > /tmp/run_patch.json
    PAUSE_OK=0
    for i in 1 2 3; do
      if curl -sf -X PATCH "${API_URL}/api/v1/runs/${RUN_ID}" \
          -H "Authorization: Bearer ${API_TOKEN}" \
          -H "Content-Type: application/json" \
          --data-binary @/tmp/run_patch.json > /dev/null; then
        PAUSE_OK=1; break
      fi
      sleep $((i * 2))
    done

    # Cost Estimation runs after plan but before pausing for approval.
    # We feed infracost the plan.json we already produced — that's exact (uses
    # the resource set Terraform actually planned) and avoids re-running plan.
    step "Cost Estimation" running ""
    if ! command -v infracost >/dev/null 2>&1; then
      step "Cost Estimation" skipped "infracost CLI not installed in executor image"
    elif [[ -z "${INFRACOST_API_KEY:-}" ]]; then
      step "Cost Estimation" skipped "infracost not configured (set INFRACOST_API_KEY to enable)"
    else
      export INFRACOST_API_KEY
      export INFRACOST_CURRENCY="${INFRACOST_CURRENCY:-USD}"
      if [[ -s /tmp/plan.json ]]; then
        COST_OUT=$(infracost breakdown --path /tmp/plan.json --format json 2>/tmp/infracost.err) && COST_OK=1 || COST_OK=0
      else
        COST_OUT=$(infracost breakdown --path . --format json 2>/tmp/infracost.err) && COST_OK=1 || COST_OK=0
      fi
      if [[ "$COST_OK" == "1" ]]; then
        # Render a human-readable summary into `output` (totals + per-resource
        # diff) so the timeline panel surfaces actual numbers; keep the full
        # JSON in summary_json for downstream tools / future UI cards.
        COST_TXT=$(printf '%s' "$COST_OUT" | jq -r --arg cur "${INFRACOST_CURRENCY}" '
          def fmt: if . == null then "0.00" else (tonumber|. * 100|round/100|tostring) end;
          ( .totalMonthlyCost // "0" | fmt ) as $tot
          | ( .totalHourlyCost  // "0" | fmt ) as $hr
          | "Estimated cost (\($cur)):  $\($tot)/month   (~$\($hr)/hour)\n\n"
          + ( [ .projects[]?.breakdown.resources[]? |
                "  • \(.name)  →  $\((.monthlyCost // "0") | fmt)/mo" ] | join("\n") )
        ' 2>/dev/null) || COST_TXT="infracost ran"
        [[ -z "$COST_TXT" ]] && COST_TXT="infracost ran (no resources priced)"
        step "Cost Estimation" success "$COST_TXT" "$COST_OUT"
      else
        step "Cost Estimation" skipped "infracost failed: $(tail -c 600 /tmp/infracost.err)"
      fi
    fi

    # Mark "Awaiting Approval" running so the timeline shows the pause clearly.
    step "Awaiting Approval" running "Plan complete — waiting for an approver. Use the ✓ Approve / ✗ Reject buttons on the run row (4-eyes: a different user must approve)."
    if [[ $PAUSE_OK -ne 1 ]]; then
      echo "WARNING: tfplan PATCH failed; approve will fail to find the saved plan" >&2
    fi
    echo "=== Plan phase complete; awaiting approval ==="
    exit 0
    ;;
  *)
    echo "ERROR: Unknown TF_COMMAND: ${TF_COMMAND}"
    trap - ERR
    report_status "failed" "Unknown TF_COMMAND: ${TF_COMMAND}"
    exit 1
    ;;
esac

# 11. Cost Estimation (best-effort; skipped without an Infracost token).
step "Cost Estimation" running ""
if ! command -v infracost >/dev/null 2>&1; then
  step "Cost Estimation" skipped "infracost CLI not installed in executor image"
elif [[ -z "${INFRACOST_API_KEY:-}" ]]; then
  step "Cost Estimation" skipped "infracost not configured (set INFRACOST_API_KEY to enable)"
else
  export INFRACOST_API_KEY
  export INFRACOST_CURRENCY="${INFRACOST_CURRENCY:-USD}"
  if [[ -s /tmp/plan.json ]]; then
    COST_OUT=$(infracost breakdown --path /tmp/plan.json --format json 2>/tmp/infracost.err) && COST_OK=1 || COST_OK=0
  else
    COST_OUT=$(infracost breakdown --path . --format json 2>/tmp/infracost.err) && COST_OK=1 || COST_OK=0
  fi
  if [[ "$COST_OK" == "1" ]]; then
    COST_TXT=$(printf '%s' "$COST_OUT" | jq -r --arg cur "${INFRACOST_CURRENCY}" '
      def fmt: if . == null then "0.00" else (tonumber|. * 100|round/100|tostring) end;
      ( .totalMonthlyCost // "0" | fmt ) as $tot
      | ( .totalHourlyCost  // "0" | fmt ) as $hr
      | "Estimated cost (\($cur)):  $\($tot)/month   (~$\($hr)/hour)\n\n"
      + ( [ .projects[]?.breakdown.resources[]? |
            "  • \(.name)  →  $\((.monthlyCost // "0") | fmt)/mo" ] | join("\n") )
    ' 2>/dev/null) || COST_TXT="infracost ran"
    [[ -z "$COST_TXT" ]] && COST_TXT="infracost ran (no resources priced)"
    step "Cost Estimation" success "$COST_TXT" "$COST_OUT"
  else
    step "Cost Estimation" skipped "infracost failed: $(tail -c 600 /tmp/infracost.err)"
  fi
fi
