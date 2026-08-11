#!/usr/bin/env bash
# Regression tests for scripts/next-version.sh — the semver bump the Release
# workflow derives from conventional commits. Builds throwaway git repos in a
# temp dir; touches nothing in this checkout. Run by CI.
#
# Usage: bash scripts/test-next-version.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="${SUT:-${HERE}/next-version.sh}"  # overridable to diff-test a variant
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT
PASS=0; FAIL=0

# Isolated git identity so the tests don't depend on (or touch) user config.
g() { git -c user.name=t -c user.email=t@example.com -c commit.gpgsign=false "$@"; }

mkrepo() { rm -rf "${TMP}/$1"; mkdir -p "${TMP}/$1"; cd "${TMP}/$1"
           g init -q -b dev; echo x > f; g add f; g commit -qm "chore: init"; }

commit() { echo "$RANDOM$1" > "f$1"; g add "f$1"; g commit -qm "$1"; }

# check <label> <expected-substring> [ENV=val ...]
check() {
  local label="$1" expect="$2"; shift 2
  local out
  # Defaults first so overrides win; release.yml always sets all five via `env:`.
  out="$(env IS_TAG=false REF_NAME=dev DEFAULT_BRANCH=dev INPUT_TAG= INPUT_BUMP= \
             "$@" bash "${SUT}" 2>&1)"
  if grep -qF -- "$expect" <<<"$out"; then
    echo "  ok   $label"; PASS=$((PASS + 1))
  else
    echo "  FAIL $label"
    echo "       expected substring: $expect"
    echo "       got: $(tr '\n' '|' <<<"$out")"
    FAIL=$((FAIL + 1))
  fi
}

mkrepo first-release
commit "feat(api): add thing"
check "no tag yet + feat → first release v0.1.0" "tag=v0.1.0"

mkrepo minor; g tag v1.2.3; commit "feat(ui): new page"
check "feat → minor" "tag=v1.3.0"

mkrepo patch; g tag v1.2.3; commit "fix(api): boom"
check "fix → patch" "tag=v1.2.4"

mkrepo perf; g tag v1.2.3; commit "perf(api): faster"
check "perf → patch" "tag=v1.2.4"

mkrepo bang; g tag v1.2.3; commit "feat(api)!: drop v1 endpoints"
check "type! → major" "tag=v2.0.0"

mkrepo footer; g tag v1.2.3
echo y > b; g add b; g commit -qm "refactor: rework config

BREAKING CHANGE: config table renamed"
check "BREAKING CHANGE footer → major" "tag=v2.0.0"

mkrepo nothing; g tag v1.2.3; commit "docs: tidy readme"; commit "ci(deploy): bump runner"
check "docs + ci only → no release" "release=false"

mkrepo mixed; g tag v1.2.3; commit "fix: small"; commit "feat: big"
check "highest level wins" "tag=v1.3.0"

mkrepo already; commit "feat: x"; g tag v0.5.0
check "HEAD already tagged → no-op" "already tagged v0.5.0"

mkrepo branch; g tag v1.2.3; commit "feat: x"
check "non-default branch → skip"        "release=false" REF_NAME=feat/x
check "push to dev when default is main" "release=false" REF_NAME=dev DEFAULT_BRANCH=main
check "tag push overrides analysis"      "tag=v9.9.9"    IS_TAG=true REF_NAME=v9.9.9
check "explicit dispatch tag overrides"  "tag=v7.7.7"    REF_NAME=feat/x INPUT_TAG=v7.7.7

mkrepo forced; g tag v1.2.3; commit "docs: nothing releasable"
check "forced major on docs-only" "tag=v2.0.0" INPUT_BUMP=major
check "forced minor on docs-only" "tag=v1.3.0" INPUT_BUMP=minor
check "forced patch on docs-only" "tag=v1.2.4" INPUT_BUMP=patch

mkrepo versionsort; g tag v0.9.0; g tag v0.10.0; commit "fix: x"
check "v0.10.0 outranks v0.9.0 (version sort, not lexical)" "tag=v0.10.1"

# Regression: the log must be matched with a here-string, not `… | grep -q`.
# `grep -q` exits on first match; if the log exceeds the pipe buffer (~64K) the
# writer takes SIGPIPE and `pipefail` reports the pipeline as failed, so a
# matching log resolves to "nothing to release". Only a log larger than the pipe
# buffer reproduces it — every other case here is a few hundred bytes.
mkrepo bigrange; g tag v1.2.3
BIG="$(printf 'filler body line %s\n' $(seq 1 6000))"   # ~130K, > pipe buffer
echo big > c; g add c; g commit -qm "chore: bulky commit

${BIG}"
commit "fix(api): the newest commit matches on line 1"
check "log larger than the pipe buffer still matches" "tag=v1.2.4"

echo
echo "next-version: passed=${PASS} failed=${FAIL}"
[ "${FAIL}" -eq 0 ]
