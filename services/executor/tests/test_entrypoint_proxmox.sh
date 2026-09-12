#!/usr/bin/env bash
# services/executor/tests/test_entrypoint_proxmox.sh
# Sources only the proxmox fan-out function from entrypoint.sh and asserts the
# exported provider vocabularies. Run: bash services/executor/tests/test_entrypoint_proxmox.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENTRYPOINT="${HERE}/../entrypoint.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT
export HOME="${TMP}/home"; mkdir -p "${HOME}"
# A fake system bundle so the CA merge has something to concatenate.
export TDT_SYSTEM_CA_BUNDLE="${TMP}/system.crt"
printf -- '-----BEGIN CERTIFICATE-----\nSYSTEM\n-----END CERTIFICATE-----\n' > "${TDT_SYSTEM_CA_BUNDLE}"

# Extract just the function body (between the markers) and source it.
sed -n '/^# >>> proxmox_wire_env/,/^# <<< proxmox_wire_env/p' "${ENTRYPOINT}" > "${TMP}/fn.sh"
# shellcheck disable=SC1090
source "${TMP}/fn.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }

# Case 1: token only, insecure.
(
  export TDT_PROXMOX_ENDPOINT="https://pve.local:8006" TDT_PROXMOX_TOKEN_ID="tdt@pve!ci" \
         TDT_PROXMOX_TOKEN_SECRET="sek" TDT_PROXMOX_TLS_INSECURE="true"
  proxmox_wire_env >/dev/null
  [[ "${PROXMOX_VE_ENDPOINT}" == "https://pve.local:8006" ]] || fail "bpg endpoint"
  [[ "${PROXMOX_VE_API_TOKEN}" == "tdt@pve!ci=sek" ]] || fail "bpg token"
  [[ "${PROXMOX_VE_INSECURE}" == "true" ]] || fail "bpg insecure"
  [[ "${PM_API_URL}" == "https://pve.local:8006/api2/json" ]] || fail "telmate url"
  [[ "${PM_API_TOKEN_ID}" == "tdt@pve!ci" && "${PM_API_TOKEN_SECRET}" == "sek" ]] || fail "telmate token"
  [[ "${PM_TLS_INSECURE}" == "true" ]] || fail "telmate insecure"
  [[ -z "${PROXMOX_VE_SSH_USERNAME:-}" && -z "${SSL_CERT_FILE:-}" ]] || fail "no ssh/ca expected"
)

# Case 2: SSH + CA bundle, secure.
(
  export TDT_PROXMOX_ENDPOINT="https://pve.local:8006" TDT_PROXMOX_TOKEN_ID="tdt@pve!ci" \
         TDT_PROXMOX_TOKEN_SECRET="sek" TDT_PROXMOX_TLS_INSECURE="false" \
         TDT_PROXMOX_SSH_USERNAME="root" TDT_PROXMOX_SSH_PRIVATE_KEY="KEYDATA" \
         TDT_PROXMOX_CA_CERT_PEM=$'-----BEGIN CERTIFICATE-----\nCUSTOM\n-----END CERTIFICATE-----'
  # Redirect to a file rather than $(…): command substitution forks, so the
  # exports would not reach the assertions below.
  proxmox_wire_env > "${TMP}/out.txt"
  out="$(cat "${TMP}/out.txt")"
  [[ "${PROXMOX_VE_SSH_USERNAME}" == "root" && "${PROXMOX_VE_SSH_PRIVATE_KEY}" == "KEYDATA" ]] || fail "bpg ssh"
  [[ "${PROXMOX_VE_INSECURE}" == "false" && "${PM_TLS_INSECURE}" == "false" ]] || fail "secure flags"
  [[ -f "${SSL_CERT_FILE}" ]] || fail "SSL_CERT_FILE missing"
  grep -q SYSTEM "${SSL_CERT_FILE}" || fail "system bundle not merged"
  grep -q CUSTOM "${SSL_CERT_FILE}" || fail "custom CA not merged"
  [[ "$(stat -c %a "${SSL_CERT_FILE}")" == "600" ]] || fail "bundle perms"
  [[ "${out}" != *sek* && "${out}" != *KEYDATA* ]] || fail "secret leaked to stdout"
)

# Case 3: custom CA set but the system bundle is missing — should still
# succeed, merge only the custom CA, and warn on stdout (no secrets).
(
  export TDT_PROXMOX_ENDPOINT="https://pve.local:8006" TDT_PROXMOX_TOKEN_ID="tdt@pve!ci" \
         TDT_PROXMOX_TOKEN_SECRET="sek" TDT_PROXMOX_TLS_INSECURE="false" \
         TDT_SYSTEM_CA_BUNDLE="${TMP}/does-not-exist.crt" \
         TDT_PROXMOX_CA_CERT_PEM=$'-----BEGIN CERTIFICATE-----\nCUSTOM\n-----END CERTIFICATE-----'
  proxmox_wire_env > "${TMP}/out3.txt"
  out="$(cat "${TMP}/out3.txt")"
  [[ -f "${SSL_CERT_FILE}" ]] || fail "SSL_CERT_FILE missing (case 3)"
  grep -q CUSTOM "${SSL_CERT_FILE}" || fail "custom CA not merged (case 3)"
  grep -q SYSTEM "${SSL_CERT_FILE}" && fail "system bundle unexpectedly present (case 3)"
  [[ "${out}" == *"WARN: system CA bundle not found"* ]] || fail "missing WARN (case 3)"
  [[ "${out}" != *sek* ]] || fail "secret leaked to stdout (case 3)"
)

echo "OK"
