#!/usr/bin/env bash
# Decide which release, if any, the current git state should produce.
#
# Writes GitHub-Actions-style `key=value` lines to STDOUT so a workflow can do
#   ./scripts/next-version.sh >> "$GITHUB_OUTPUT"
# All human-readable logging goes to STDERR (so it still shows in the run log
# without polluting the outputs). Consumed by .github/workflows/release.yml.
#
# Inputs — all env, all optional; the defaults suit a local dry run:
#   IS_TAG          "true" when the triggering ref is a tag
#   REF_NAME        branch or tag name that triggered the run
#   DEFAULT_BRANCH  the repo's default branch; only it may auto-release
#   INPUT_TAG       explicit tag from a manual dispatch (wins if set)
#   INPUT_BUMP      auto|patch|minor|major (default: auto)
#
# Outputs:
#   release=true|false
#   tag=vX.Y.Z      (only when release=true)
#
# Bump rules — conventional-commit subjects since the latest v*.*.* tag:
#   `<type>!:` or a `BREAKING CHANGE` footer      → major
#   `feat:`                                       → minor
#   `fix:` / `perf:`                              → patch
#   anything else (docs/chore/ci/test/refactor)   → no release
#
# Lives here rather than inline in the workflow so it can be regression-tested
# (scripts/test-next-version.sh, run by CI). The inline version shipped with a
# bug that the tests caught: `git describe --exact-match` ignores lightweight
# tags without `--tags`, and the tags this repo cuts are lightweight.
set -euo pipefail

IS_TAG="${IS_TAG:-false}"
REF_NAME="${REF_NAME:-}"
DEFAULT_BRANCH="${DEFAULT_BRANCH:-}"
INPUT_TAG="${INPUT_TAG:-}"
INPUT_BUMP="${INPUT_BUMP:-}"

log()  { echo "$*" >&2; }
skip() { echo "release=false"; log "$1"; exit 0; }

# ── 1. an explicit tag wins: a tag push, or a dispatch that named one
if [ "${IS_TAG}" = "true" ]; then
  log "Tag push: ${REF_NAME}"
  echo "release=true"; echo "tag=${REF_NAME}"; exit 0
fi
if [ -n "${INPUT_TAG}" ]; then
  log "Explicit tag requested: ${INPUT_TAG}"
  echo "release=true"; echo "tag=${INPUT_TAG}"; exit 0
fi

# ── 2. auto-compute — only ever from the repo's own default branch
if [ "${REF_NAME}" != "${DEFAULT_BRANCH}" ]; then
  skip "Ref '${REF_NAME}' is not the default branch ('${DEFAULT_BRANCH}') — skipping."
fi

# HEAD already released → idempotent no-op, so re-runs are harmless.
# `--tags` is load-bearing: without it describe only considers *annotated* tags,
# and the tags this workflow creates are lightweight.
if EXISTING="$(git describe --tags --exact-match --match 'v*.*.*' HEAD 2>/dev/null)"; then
  skip "HEAD is already tagged ${EXISTING} — nothing to release."
fi

# `--sort=-v:refname` is a version sort, not lexical: v0.10.0 must beat v0.9.0.
PREV="$(git tag -l 'v*.*.*' --sort=-v:refname | head -n1)"
if [ -z "${PREV}" ]; then
  PREV="v0.0.0"
  RANGE="HEAD"            # no tag yet → the whole history is "since"
else
  RANGE="${PREV}..HEAD"
fi
LOG="$(git log --format='%s%n%b' "${RANGE}")"
log "Commits since ${PREV}:"
git log --format='  %h %s' "${RANGE}" >&2

# ── 3. bump level
#
# A here-string, NOT `printf ... | grep -q`. `grep -q` exits the instant it
# matches, so on a log bigger than the pipe buffer (~64K — i.e. any real
# release range) the writer takes SIGPIPE, `pipefail` promotes that to a
# non-zero pipeline status, and a log that *did* match reports "no match".
# That bug shipped in the first draft of this script: 17 unit tests passed
# because their synthetic repos were a few hundred bytes, while the real
# 135K history silently resolved to "nothing to release".
has() { grep -qE "$1" <<<"${LOG}"; }

LEVEL="${INPUT_BUMP:-auto}"
if [ "${LEVEL}" = "auto" ] || [ -z "${LEVEL}" ]; then
  if has '^[a-z]+(\([^)]*\))?!:' || has '^BREAKING[ -]CHANGE'; then
    LEVEL=major
  elif has '^feat(\([^)]*\))?:'; then
    LEVEL=minor
  elif has '^(fix|perf)(\([^)]*\))?:'; then
    LEVEL=patch
  else
    skip "No feat/fix/perf/breaking commits since ${PREV} — nothing to release."
  fi
fi

IFS=. read -r MA MI PA <<<"${PREV#v}"
case "${LEVEL}" in
  major) MA=$((MA + 1)); MI=0; PA=0 ;;
  minor) MI=$((MI + 1)); PA=0 ;;
  patch) PA=$((PA + 1)) ;;
  *) log "::error::unknown bump level '${LEVEL}'"; exit 1 ;;
esac

log "${PREV} → v${MA}.${MI}.${PA} (${LEVEL})"
echo "release=true"
echo "tag=v${MA}.${MI}.${PA}"
