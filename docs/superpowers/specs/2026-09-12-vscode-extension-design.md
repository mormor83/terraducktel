# Terraducktel VS Code extension — design

**Date:** 2026-09-12
**Status:** approved design, pending implementation plan
**Branch:** `vscode-extension` (from `main`)

## Problem

Terraducktel (TDT) is driven from the browser UI or the `tdt` CLI. Operators
who live in VS Code editing the Terraform repos that TDT orchestrates have to
switch context to see which workspace a file belongs to, trigger a plan, read
the diff, and approve — and they miss runs waiting on them. The extension
brings those loops into the editor without duplicating the web UI.

## Decision summary

| Question | Decision |
|---|---|
| Scope of v1 | All four workflows, staged in three milestones: **A** sidebar + trigger/approve, **B** editor ↔ workspace mapping, **C** approval notifications. Each milestone ships on its own. |
| Architecture | **Approach A**: native VS Code views over a typed TypeScript API client, polling. No webview-embedded SPA; no new streaming endpoint on the API (can be added later without changing the extension's shape). |
| Transport | Direct API client (fetch). The `tdt` CLI is not a dependency: it has no JSON output and would require a Python install. |
| Sign-in | All three modes from day one: email + password (self-renewing refresh token), pasted API key, and SSO via the server's existing CLI loopback hand-off. |
| Contract | The extension keeps its own `api_contract.json`, guarded by a backend test cloned from `test_cli_api_contract.py`, so a router change fails CI naming the extension feature it broke. |

## 1. Repository layout and toolchain

```
services/vscode/
  package.json            # extension manifest: views, commands, menus, settings, activation events
  api_contract.json       # every endpoint the extension calls (guarded, see §7)
  src/
    extension.ts          # activate(): wires the pieces below, nothing else
    api/client.ts         # typed fetch client: base URL, X-Business-Unit, bearer, retry-on-401 via refresh
    api/types.ts          # response types for the endpoints in the contract
    auth/tokenManager.ts  # SecretStorage-backed credential store + refresh logic
    auth/sso.ts           # loopback listener + browser hand-off
    auth/profiles.ts      # profiles from settings; active profile/BU state
    state/store.ts        # polling cache of workspaces/runs/drift for the current BU; emits change events
    views/workspacesTree.ts
    views/runsTree.ts
    commands/*.ts         # one file per command group (auth, workspace, run, approval)
    editor/mapping.ts     # file → workspace resolution (milestone B)
    editor/statusBar.ts
    notifications/approvals.ts   # milestone C
    output/runOutput.ts   # step tailing into an OutputChannel
    output/planDocument.ts       # tdt-plan: virtual document provider
  test/
    unit/                 # client, tokenManager, mapping — against a fake HTTP server
    integration/          # @vscode/test-electron smoke: activate, sign in with API key against a stub, render tree
  README.md
```

- TypeScript, strict. Bundled with esbuild to one `dist/extension.js`. Node ≥ 20
  (VS Code's runtime), `engines.vscode ^1.90`.
- Lint/format with the repo's existing conventions; no new colour tokens
  (VS Code theme colours only).
- Publisher/id are the operator's decision at Marketplace time; the manifest
  ships with `publisher: "terraducktel"` as a placeholder and CI never
  publishes.

## 2. Connection and sign-in

**Profiles** — `terraducktel.profiles: [{name, url, bu, insecureTls?}]` and
`terraducktel.activeProfile` in VS Code settings. This mirrors the CLI's
`~/.config/tdt/config.toml` shape so people can copy values across; the
extension does **not** read the CLI's files.

**Secrets** — one `SecretStorage` entry per profile,
`terraducktel.cred.<profile>`, holding JSON `{kind: "password"|"api_key"|"sso",
refresh_token?, api_key?}`. Access tokens live in memory only. Nothing secret
is ever written to settings, logs, or the output channel.

**Modes** (`Terraducktel: Sign in` command):

1. Ask the server `GET /auth/config`. If `oidc_enabled && cli_loopback`,
   offer SSO first; otherwise password. API key is always offered.
2. **Password** → `POST /auth/token` with email + password from input boxes
   (password box masked). Store the refresh token.
3. **API key** → validate the `tdt_` prefix, store as-is. API keys force their
   own BU; the BU switcher is disabled for such profiles.
4. **SSO** → start an `http.Server` on `127.0.0.1:0`, generate a 32-char
   url-safe nonce, `vscode.env.openExternal(`${url}/api/v1/auth/oidc/login?cli_port=<port>&cli_nonce=<nonce>`)`.
   The server's existing hand-off page bounces the browser to
   `http://127.0.0.1:<port>/callback` carrying the token pair and the nonce;
   the listener verifies the nonce, stores the refresh token, replies with a
   "you can close this tab" page, and shuts down. Timeout 5 minutes; a second
   sign-in cancels the first listener. Over Remote-SSH the listener runs on the
   remote, so the command detects `vscode.env.remoteName` and tells the user to
   use an API key instead (same guidance as the CLI).

**Refresh** — the client sends the access token; on 401 it calls
`POST /auth/refresh` once, swaps both tokens, and retries the request once. A
second 401 signs the profile out and surfaces a "Sign in again" notification.
Refresh is serialised so concurrent requests don't race for the same refresh
token.

**BU** — `terraducktel.bu` per profile; a sidebar title button lists
`GET /business-units` and switches. The client sends `X-Business-Unit: <slug>`
on every request. Superadmins get an `all` entry.

## 3. Milestone A — sidebar

**Workspaces view** (`terraducktel.workspaces`) — grouped exactly as the web
tree: provider → account (AWS account / Azure subscription / GCP project /
Proxmox cluster / …) → region → folders → leaf. Grouping reuses the same path
convention rules the web `paths.ts` implements, ported to TypeScript in
`state/grouping.ts` (not shared code — the UI package is not importable from
the extension — but the unit tests carry the same fixtures so the two can't
drift silently). Each leaf shows: name, drift badge, last run status icon,
tracked branch. Tooltip: id, `tf_working_dir`, repo, environment, tags.

**Runs view** (`terraducktel.runs`) — recent runs for the BU, newest first,
filterable by status (awaiting approval first). Each run expands to its steps
with status icons (`GET /runs/{id}/steps?include_output=false`, fetched lazily
on expand and cached per run once the run reaches a terminal status). The
status *filter* is deferred past milestone A — see the deviations below.

**Data** — `state/store.ts` polls `GET /workspaces` and `GET /runs?limit=200`
every `terraducktel.refreshIntervalSeconds` (default 30) while a view is
visible, and on demand via a refresh button. One in-flight poll at a time;
backs off to 5 minutes after three consecutive failures and shows a warning
item at the top of the tree. Polling is gated twice over: the timer skips the
network while neither view is visible (`Store.setActive`), and the store's
client getter yields `undefined` while signed out, so a signed-out window is
silent rather than issuing credential-less requests. Drift comes from each
workspace's own `drift_status`; `GET /drift/summary` is not called.

**Context menu actions** — workspace: Plan, Apply, Destroy, Set branch, Sync
from repo, Open in browser, Copy id. Run: Show steps, Show plan, Approve,
Reject, Cancel, Open in browser. Menus are gated by the user's role from the
token where visible (viewer sees no write actions); the server remains the
authority.

## 4. Milestone A — trigger and approve

**Plan / Apply / Destroy** → `POST /workspaces/{id}/runs {command}`; optional
branch pin first via `PUT /workspaces/{id} {repo_ref}` when the user picks one
from `GET /workspaces/{id}/branches`. Destroy asks the user to type the
workspace name (same guard as the web UI).

**Step tailing** → an `OutputChannel` per run ("TDT run <short id> — <ws>"),
polling `GET /runs/{id}/steps?since=<position>` every 2 s while the run is
non-terminal, appending only new steps/output. Terminal states stop the poll
and print a one-line summary. `Terraducktel: Watch run…` attaches to any run.

**Plan output** → `GET /runs/{id}/plan` rendered as a read-only virtual
document `tdt-plan:<label>.tfplan.txt?run=<run id>` with the `terraform`
language id when available (best-effort `setTextDocumentLanguage`, silently
skipped when no Terraform grammar is installed) and a small diff decorator
(`+`/`-`/`~` line colouring via `editorGutter`-style decorations). The content
provider is async and re-fetches by the URI's `run` query when its cache is
cold, so a plan tab restored on the next window reload loads rather than
sticking on "(loading…)". Offered — not force-opened — when a plan reaches
`awaiting_approval` from the extension; also available on demand.

**Approve / Reject** → before `POST /runs/{id}/approve`, fetch
`GET /runs/{id}/graph` and show a modal: "Apply N to add, N to change, N to
destroy, N to replace on <workspace>?" with Approve / Show plan / Cancel.
Reject prompts for an optional reason. Both refresh the trees afterwards.
Cancel is offered only for `pending | running | planning | awaiting_approval`;
a `planned` run has nothing left to cancel.

## Milestone A deviations

Shipped deliberately differently from the sections above. Each is a decision,
not an oversight — revisit them by name rather than re-deriving them.

1. **Runs-view status filter deferred.** §3 promises a status filter; milestone
   A ships a fixed sort instead (awaiting-approval → active → landed, newest
   first within a status, unknown statuses last) plus the approval-count badge.
   A filter only earns its keep once a BU's run list is long enough to hide
   things in, and the sort answers "what needs me?" on its own.
2. **The plan is offered, never force-opened.** §4 said "opened automatically"
   when a watched run reaches `awaiting_approval`. Stealing the active editor
   from someone mid-edit is hostile, so the landing toast carries a **Show
   plan** action (alongside **Approve…**) and the document opens on a click.
3. **Unlinked non-AWS workspaces group under their top folder, not "AWS
   global".** The web tree buckets a workspace with no recognised provider
   under the AWS `global` account. `state/grouping.ts` instead classifies it as
   `other` and labels the group with its top path segment (e.g.
   `cloudflare/…` → **cloudflare**). Same inputs, more honest label; the AWS
   fixtures in `grouping.test.ts` still mirror `paths.test.ts` exactly.
4. **`GET /drift/summary` is not called.** §3 listed it as a third poll. Every
   workspace row already carries `drift_status` from `GET /workspaces`, so the
   extra request bought nothing but load. It is not in `api_contract.json`.

## 5. Milestone B — editor ↔ workspace mapping

- On active-editor change for `*.tf`/`*.tfvars`/`*.hcl`, compute the file's
  path relative to the enclosing git root (`git rev-parse --show-toplevel`,
  cached per folder) and the folder containing it.
- Resolve the workspace whose `repo_url` matches the folder's `origin` (host +
  path, `.git`-insensitive) **and** whose `tf_working_dir` is the longest
  prefix of the relative directory. `local://` workspaces match by
  `tf_working_dir` alone.
- Status bar item: `$(cloud) TDT: <workspace> · <last run status>`; click →
  quick pick: Plan this leaf, Show last plan, Reveal in sidebar, Open in
  browser. When no workspace matches, the item shows `TDT: not imported` with
  an action to open the Discover flow in the browser.
- Branch awareness: if the current git branch ≠ `repo_ref`, "Plan this leaf"
  offers "Plan on <branch> (pins the workspace)" vs "Plan on <repo_ref>".

## 6. Milestone C — approval notifications

- Background poll of `GET /runs?status=awaiting_approval` for the active BU
  (and, for superadmins, `all`) every `terraducktel.approvals.pollSeconds`
  (default 60, 0 disables). Runs already notified are remembered per session
  and in `globalState` for 24 h so a reload doesn't re-notify.
- Each new run → `showInformationMessage` with **Approve**, **Reject**,
  **Open**. Approve goes through the §4 modal; nothing is applied from a
  notification click alone.
- A count badge on the Runs view reflects pending approvals.

## 7. Contract, security, error handling

- `services/vscode/api_contract.json` lists every `{method, path, used_by}`
  the extension calls — the auth four, `/business-units`, `/workspaces`
  list/put, `/workspaces/{id}/branches`, `/workspaces/{id}/sync`,
  `/workspaces/{id}/runs`, `/runs` list/get/steps/graph/plan/approve/reject/cancel.
  `services/api/tests/test_vscode_api_contract.py` is a copy of the CLI guard
  pointed at this file. The file is the *used* surface, not a wish list: the
  single-workspace `GET /workspaces/{id}` and `/drift/summary` are both absent
  because nothing calls them (the list response carries everything the tree
  renders).
- A request with no credential at all never reaches the network: the client
  throws `ApiError(401, "Not signed in")` before sending, so a poll that fires
  while signed out cannot trip the refresh-then-sign-out path. A refresh that
  fails *transiently* (network, timeout, 5xx) is rethrown and fails only the
  original request; only a 4xx rejection of the refresh token deletes the
  stored credential and signs the session out.
- TLS verified by default; `insecureTls: true` per profile disables
  verification for self-signed dev stacks and shows a warning item in the
  tree while active.
- Errors: every command surfaces the API's `detail` string in a VS Code error
  message; the underlying request/response (minus auth headers) goes to a
  "Terraducktel" log output channel at `trace` level only when
  `terraducktel.trace` is on.
- No telemetry.

## 8. Testing

- **Unit (vitest or mocha, Node):** `api/client.ts` against an in-process
  fake HTTP server — bearer + BU headers, 401 → refresh → retry once, second
  401 → signed-out event, refresh serialisation under concurrency, error
  `detail` propagation; `auth/tokenManager.ts` with an in-memory SecretStorage;
  `auth/sso.ts` nonce verification and timeout; `state/grouping.ts` with the
  same fixtures as the web `paths.test.ts`; `editor/mapping.ts` longest-prefix
  and repo matching incl. `local://`.
- **Integration (`@vscode/test-electron`):** activate the extension against a
  stub TDT server (Node `http`), sign in with an API key, assert the
  workspaces tree renders the stub's workspaces, trigger a plan and assert the
  output channel received the stub's steps, approve and assert the POST.
- **Backend:** `test_vscode_api_contract.py`.
- **Manual, once per milestone:** against the local compose stack with
  `admin@test.com`: password sign-in, tree renders the real workspaces, plan a
  workspace, watch steps, open plan, approve a no-op apply; SSO only where an
  OIDC deployment exists.

## 9. Delivery and documentation

- `make test-vscode` (unit + integration), `make build-vscode` → `.vsix`.
- CI: a `vscode` job in `.github/workflows/ci-cd.yml` runs unit tests and
  uploads the `.vsix` as an artifact; no Marketplace publish step.
- Docs: `docs/VSCODE.md` (install from `.vsix`, profiles, sign-in modes,
  commands); a `services/vscode/` row in `CLAUDE.md`'s repo map and the
  `docs/ARCHITECTURE.md` client section next to the CLI.

## Out of scope (v1)

- Marketplace publishing and signing.
- A streaming endpoint (SSE/WebSocket) on the API.
- Editing workspace settings (variables, tags, drift schedule) from the
  extension — read-only display only; use the web UI.
- Creating/importing workspaces (Discover) — link out to the browser.
- Sharing credential files with the `tdt` CLI.
- Web-based VS Code (vscode.dev): the loopback listener and git shell-outs
  assume a desktop or Remote host.
