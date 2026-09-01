# The `tdt` CLI

`tdt` is how Terraducktel is meant to be driven from a terminal, from CI, and
from an AI agent. It replaces hand-rolled `curl` against the HTTP API.

Its `--help` is generated from the command surface, so it cannot drift from the
binary you have installed. When this page and `tdt --help` disagree, believe
`--help`.

## Install

```bash
uv tool install ./services/cli      # or: pipx install ./services/cli
tdt --version
```

## Point it at a deployment

A **profile** bundles the two things every request needs — the API base URL and
the business-unit slug — so they stop being environment variables you re-export
in every shell.

```bash
tdt profile add prod --url https://tdt.example.com --bu platform --default
tdt login
tdt whoami
```

Config lives in `~/.config/tdt/config.toml`; credentials in
`~/.config/tdt/credentials.json`, created `0600`.

### Signing in

`tdt login` asks the server which providers are enabled (`GET /auth/config`) and
picks for you:

| Flag | When |
|---|---|
| `--sso` | OIDC is configured. Opens a browser; the loopback flow hands the token pair back. Default when available. |
| `--password` | No OIDC. Prompts for email and password. |
| `--api-key` | CI and other unattended callers. Paste a `tdt_…` key minted in the UI. |

The first two store a refresh token and **renew themselves**, so signing in is a
once-per-machine step rather than a daily one. API keys have nothing to refresh:
they are long-lived by design, and they force their own business unit, so `--bu`
is ignored for them.

!!! note "`--sso` needs a browser on the same machine"
    The flow binds a listener on `127.0.0.1`, so over SSH the callback has
    nowhere to land. Use `--api-key` there. If the deployment predates the
    loopback hand-off the CLI says so immediately rather than hanging.

## The core loop

```bash
tdt run plan    my-workspace                     # ends at `planned`, no approval
tdt run apply   my-workspace                     # shows the diff, prompts you
tdt run apply   my-workspace -b feat/my-branch   # pin a ref first (pre-merge)
tdt run destroy my-workspace                     # real terraform destroy
```

Workspaces are addressable by **name or id**. An ambiguous name is an error,
never a silent pick.

### Approval gates

This is the reason the CLI exists. Unattended, the safety check belongs in a
flag rather than in whoever is watching:

```bash
tdt run apply my-workspace --auto-approve --max-destroy 0
tdt run apply my-workspace --auto-approve --require-noop     # expect 0/0/0/0
```

Gates are evaluated on **every** run that reaches `awaiting_approval`, whether
or not `--auto-approve` was passed, and a violated gate **rejects** the run —
nothing is applied. Available gates: `--require-noop`, `--max-add`,
`--max-change`, `--max-destroy`, `--max-replace`.

Without `--auto-approve` and without a TTY, the command exits `4` rather than
hanging on a prompt nobody can see.

!!! warning "`tdt run destroy` is the only command that deletes infrastructure"
    `tdt ws delete` merely drops Terraducktel's tracking row and leaves the
    resources and the state file intact. On a TTY, `destroy` makes you type the
    workspace name; in automation it requires `--yes`, and should be paired
    with `--max-destroy N` so an unexpectedly large teardown is refused.

## Reading state

```bash
tdt context -o json                    # whole-BU snapshot in one call
tdt ws list                            # add -o json for ids
tdt ws list -t team=payments           # filter by tag; repeatable and AND-ed
tdt ws get my-workspace
tdt run list -w my-workspace --status failed -n 5
tdt run steps <run-id> --logs          # step timeline, with output
tdt run graph <run-id>                 # the +/~/-/± diff and changed addresses
tdt run watch <run-id>                 # follow an in-flight run
tdt drift summary
tdt show bus | tdt show accounts | tdt show clusters
```

`-o json` works on every command and returns the API payload untouched — prefer
it when parsing. `tdt context` is built for agents and scripts: it replaces four
or five separate calls with one snapshot of workspaces, recent runs and drift.

## Tags

```bash
tdt ws tag my-workspace -s team=payments -s tier=prod   # merges
tdt ws tag my-workspace -u tier                         # remove a key
tdt ws tag ws-a ws-b ws-c -s owner=platform             # batch
tdt ws tags                                             # every key, with counts
```

`--set` merges per key, so retagging a batch's `team` never wipes an `owner` tag
one of them carries. A batch is all-or-nothing: if any workspace is outside your
business unit the whole request is rejected, so you are never left guessing which
half applied. Keys are lowercased by the API; values keep their case.

## Exit codes

Branch on these rather than parsing output. They are a stable contract.

| Code | Meaning |
|---|---|
| `0` | success |
| `1` | usage or configuration error |
| `2` | authentication — run `tdt login` |
| `3` | plan phase failed (terraform, checkov, OPA) |
| `4` | rejected: by you, or by a `--max-*` / `--require-noop` gate |
| `5` | apply phase failed |
| `6` | timed out waiting (the run continues server-side) |
| `7` | API error (404/409/5xx, transport) |

On `3` and `5` the CLI prints the failed step and the tail of its log.

A `403` is **not** an auth-recovery case: it means the credential's role,
business unit or API-key capability doesn't cover the call. Logging in again
will not help.

## Global options

`--profile/-p`, `--url`, `--bu` and `-o/--output` may appear either before or
after the command — `tdt whoami --url …` and `tdt --url … whoami` are the same.
The exception is a subcommand that declares the same name itself, such as
`tdt profile add --url`, which keeps its own.

## Using it from an agent

The CLI ships a Claude skill. Inside a clone of this repository it is already
active at `.claude/skills/tdt/`; elsewhere:

```bash
tdt skill install     # → ~/.claude/skills/tdt/
```

An agent driving `tdt` gets the approval gates for free, which is the point: with
`--max-destroy 0`, "review the diff before approving" stops being an instruction
that can be skipped and becomes a check that runs.

## Gotchas

- **The executor has no `aws` CLI.** A helm/kubernetes provider using exec-auth
  (`aws eks get-token`) fails with `executable aws not found`; use SDK token auth
  (`data.aws_eks_cluster_auth`).
- **The executor reaches public cloud APIs but not private VPC endpoints**
  without a network path. Symptom: `dial tcp …:443: i/o timeout` at apply.
- **A connection failure is usually the network**, not auth — many deployments
  put the API behind an internal load balancer. The CLI says so explicitly.
- **Terraducktel imports, it does not scaffold.** A workspace needs its repo
  directory to exist already; `tdt ws discover --repo <url>` lists importable
  leaves.
