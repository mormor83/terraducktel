---
name: tdt
description: Use when driving Terraducktel (TDT) from the terminal — planning, reviewing, approving and applying a workspace, checking run status or the plan diff, listing workspaces/runs/drift, or scripting TDT in CI. Wraps the `tdt` CLI; do not hand-roll curl against the API.
---

# Terraducktel (TDT)

Self-hosted Terraform/Helm orchestration. A **workspace** is one leaf directory
plus its S3 backend; a **run** goes `checkov → plan → cost → approval → apply`.

**Drive it with the `tdt` CLI, not curl.** The CLI owns auth (including token
refresh), name→id resolution, polling, and the approval gate. Its `--help` is
generated from the live API, so it cannot go stale the way a hand-written
endpoint table does.

## Setup (once per machine)

```bash
uv tool install /path/to/terraducktel/services/cli   # or: pipx install
tdt profile add prod --url https://tdt.example.com --bu <slug> --default
tdt login          # picks browser SSO or password automatically; --api-key for CI
```

`tdt login` asks the server which providers are enabled and uses browser SSO
when OIDC is on, a password prompt otherwise. It stores credentials in
`~/.config/tdt/credentials.json` (0600) and **refreshes them itself** — you will
not be asked for an hourly token. If a command reports exit 2, run `tdt login`
again; that is the only auth recovery step. In CI use `--api-key` (no browser,
no refresh).

A 403 is *not* an auth-recovery case — it means the credential's role, BU or API-key
capability doesn't cover the call. Re-logging in won't help.

## The core flow

```bash
tdt run plan    <workspace>                    # ends at `planned`, no approval
tdt run apply   <workspace>                    # prompts you to approve the diff
tdt run apply   <workspace> -b feat/my-branch  # pin a ref first (pre-merge)
tdt run destroy <workspace>                    # real terraform destroy — see below
```

Unattended, put the safety check in flags rather than in your own judgement:

```bash
tdt run apply <workspace> --auto-approve --max-destroy 0
tdt run apply <workspace> --auto-approve --require-noop     # expect a 0/0/0/0 plan
```

Gates are enforced whether or not `--auto-approve` is set, and a violated gate
**rejects** the run — nothing is applied. Without `--auto-approve` and without a
TTY you get exit 4 instead of a hang.

`tdt run destroy` is the **only** command that deletes infrastructure. On a TTY
it makes you type the workspace name; in automation it needs `--yes`, and should
be paired with `--max-destroy N` so an unexpectedly large plan is refused:

```bash
tdt run destroy <workspace> --yes --auto-approve --max-destroy 1
```

## Reading state

```bash
tdt context -o json                  # one-shot BU snapshot: workspaces, recent runs, drift
tdt ws list                          # add -o json for ids
tdt ws list -t team=payments         # filter by tag; repeatable and AND-ed
tdt ws tags                          # every tag key in the BU, with counts
tdt ws tag <ws> -s team=payments     # set/unset tags; --set merges, never clobbers
tdt run list -w <workspace> --status failed -n 5
tdt run get <run-id>
tdt run graph <run-id>               # the +/~/-/± diff and changed addresses
tdt run steps <run-id> --logs        # step timeline; --logs includes output
tdt run watch <run-id>               # follow an in-flight run
tdt drift summary
```

Every command takes `-o json` for machine-readable output — prefer it when
parsing. Workspaces are addressable by **name or id**; an ambiguous name is an
error, never a silent pick.

## Exit codes (branch on these, don't parse output)

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | usage / config error |
| 2 | auth — run `tdt login` |
| 3 | plan phase failed (terraform, checkov, OPA) |
| 4 | rejected: by you, or by a `--max-*` / `--require-noop` gate |
| 5 | apply phase failed |
| 6 | timed out waiting (the run continues server-side) |
| 7 | API error (404/409/5xx, transport) |

On exit 3 or 5 the CLI already prints the failed step and the tail of its log.

## Environments: compare & promote (API only, no CLI wrapper yet)

Governance › Environments links a **source** repo-tree node to a **target**
(account, region, folder or single stack; both at the same level), pairs their
stacks, and diffs **repo config** (a semantic HCL diff, each side read at its own
pinned branch) and **live state** (the tfstate TDT holds; secrets redacted).
The CLI doesn't wrap this yet, so call the API (base `<url>/api/v1`, header
`X-Business-Unit: <slug>`):

```text
POST /env-links/preview-pairs          {source_node:{level,path}, target_node:{level,path}}   dry-run pairing, viewer
GET  /env-links                        links + pair_summary
POST /env-links                        {name, source_node, target_node, rewrite_rules?, pair_overrides?, protected_rules?}
GET  /env-links/{id}/pairs?status=     pairs (not_compared|in_sync|diverged|missing_in_target|missing_in_source|excluded)
POST /env-links/{id}/compare           {pair_ids?, direction?: forward|reverse, force?}   queue compares → 202
GET  /env-pairs/{pair}/compare?direction=   200 result | 202 computing (retry): config_diff.hunks[], state_diff, refs
POST /env-links/{id}/promotions/preview     selection → exact file changes, blockers[], warnings[]
POST /env-links/{id}/promotions             commit + ordinary apply runs (201; 200 replayed; 409 blockers)
GET  /promotions/{id}                  status + per-stack stages (checkov/plan/opa/cost/approval/apply/verified)
```

- **Hunks** are `promotable`, `protected` (per-link key/value rules: account
  ids, sizing, env names…) or `backend` (never promotable). A diff of only
  protected/backend hunks counts as `in_sync`.
- **Link / promote / revert are BU-admin and interactive-only.** API keys get
  403 at every tier, so log in with SSO for those. Reads (preview-pairs,
  compare, pairs, promotions) work with a key.
- **Promotion never bypasses the gate.** It commits to the target's pinned
  branch and creates normal `apply` runs that wait for approval like any other.
  It needs the BU's git write access enabled (Settings → GitHub; off by default).
- **Compare reads the tfstate TDT stores.** It isn't a live cloud scan and never
  shows secret values. To diff what is actually in AWS (e.g. Secrets Manager
  contents), use AWS directly.

## Gotchas that are still true

- **The executor has no `aws` CLI.** A helm/kubernetes provider using exec-auth
  (`aws eks get-token`) fails with `executable aws not found` — use SDK token
  auth (`data.aws_eks_cluster_auth`) instead.
- **The executor reaches public AWS APIs but not private VPC endpoints** (e.g. a
  private EKS API) without a network path. Symptom: `dial tcp …:443: i/o timeout`
  at apply.
- **A connect failure is usually the VPN**, not auth — the API commonly resolves
  to an internal load balancer. The CLI says so explicitly on `ConnectError`.
- **Workspace creation needs the repo leaf to already exist.** TDT imports, it
  does not scaffold. `tdt ws discover --repo <url>` lists importable leaves.
- **`tdt ws delete` never destroys infra.** It drops TDT's tracking row and
  leaves infra + tfstate intact; git-synced workspaces need `--force`.
  `tdt run destroy` is the one that tears resources down.
- **Any operator may approve any run** in their BU, including the one who
  triggered it. 4-eyes was deliberately revoked — don't reintroduce it.

## When the CLI can't do it

`tdt --help` and `tdt <group> --help` are authoritative. For endpoints the CLI
doesn't wrap, the API's OpenAPI schema is at `<url>/api/openapi.json`. If you add
a new API call to the CLI, add it to `services/cli/api_contract.json` too — two
tests use that file to keep the CLI and API from drifting apart.
