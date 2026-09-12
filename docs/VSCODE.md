# Terraducktel for VS Code

Drive TDT from the editor: a Workspaces tree grouped like the web UI, a Runs
tree, plan/apply/destroy with live step output, the plan as a diff-coloured
document, and gated approvals.

## Install

Build the `.vsix` and install it:

```bash
cd services/vscode && npm ci && npm run package
code --install-extension terraducktel-vscode.vsix
```

(CI also uploads `terraducktel-vscode.vsix` as an artifact on every push.)

## Configure a profile

Since 0.3.1 profiles are name→url maps, so every row renders natively in
**Settings → Extensions → Terraducktel** (no JSON array to hand-edit):

- `terraducktel.profiles` — profile name → API origin.
- `terraducktel.uiUrls` — profile name → web UI origin, only when it differs
  from the API origin (as in the dev compose stack).
- `terraducktel.insecureTlsProfiles` — profile names that skip TLS
  certificate verification (self-signed dev stacks only).

The easiest way in is the **Terraducktel: Add profile…** command: it prompts
for a name (`^[a-z0-9][a-z0-9._-]{0,39}$`), the API origin, an optional UI
origin, and whether to skip TLS verification, writes all three settings at
User scope, makes the new profile active, and offers to sign in immediately.
**Terraducktel: Remove profile…** is the inverse — quick pick, a confirm
modal, then it clears the profile from all three settings and deletes its
stored credential.

The active profile is **not** a setting anymore: use
**Terraducktel: Switch profile** (the sidebar's title-bar `$(server)` button,
or the profile status bar item) to pick which one is active. It's kept in
the extension's global state, per VS Code installation, not in
`settings.json`.

`settings.json` equivalent of the map form:

```json
"terraducktel.profiles": {
  "local": "http://localhost:8001",
  "prod":  "https://tdt.example.com"
},
"terraducktel.uiUrls": {
  "local": "http://localhost:3001"
},
"terraducktel.insecureTlsProfiles": []
```

The legacy array form (`terraducktel.profiles: [{name, url, uiUrl?, bu?,
insecureTls?}]`, from before 0.3.1) is still read for backward compatibility
and merges with the map form if both are somehow present (the map wins for a
same-named profile) — but it no longer renders as editable rows in the
Settings UI, so prefer the map form or the wizard for anything new.
`terraducktel.activeProfile` is deprecated and read only once, to migrate a
pre-0.3.1 value into the new global-state-backed active profile.

The business unit is chosen in-app (**Switch business unit**) and remembered
per profile; the old per-profile `bu` field is migrated automatically the
first time profiles are rewritten (by **Add profile…** or **Remove
profile…** — the map schema has no room for a `bu` column, so it can't stay
in settings, but nothing already-remembered is lost).

## Sign in

Command Palette → **Terraducktel: Sign in**. The extension asks the server
which providers exist and offers:

| Mode | Notes |
|---|---|
| SSO | Opens your browser; the server hands the tokens back on `127.0.0.1` (same flow as `tdt login --sso`). Not available in Remote-SSH — use an API key there. |
| Email + password | Stores only the refresh token (VS Code SecretStorage); renews itself. |
| API key | Paste a `tdt_…` key minted in the UI. Bound to one business unit. |

Nothing secret is written to settings or logs.

## Use

- **Workspaces** view: provider → account → region → folders → workspace, each
  with drift and last-run status; expand a workspace for its recent runs.
  Right-click for Plan / Apply… / Destroy… / Set tracked branch / Sync from
  repo / Open in browser / Copy id.
- **Runs** view: newest first, awaiting-approval on top (badge = count).
  Click a run to watch its steps in an output channel, or expand it to see its
  steps with status icons and durations. Inline icons: watch, plan output and
  (for a run awaiting approval) Approve…; **Reject…** and **Cancel run** are on
  the right-click menu.
- **Approve…** shows `+add ~change -destroy ±replace` from the plan graph and
  needs an explicit click; **Destroy…** asks you to type the workspace name.
- Title-bar buttons: switch profile (only shown once at least one profile
  exists), refresh, switch business unit.

Settings: `refreshIntervalSeconds` (default 30), `runsLimit` (200), `trace`
(request metadata to the *Terraducktel* output channel; never credentials).

## Approval notifications

While signed in, the extension polls `GET /runs?status=awaiting_approval` for
the active business unit every `terraducktel.approvals.pollSeconds` (default
60; values below 15 are treated as 15; 0 disables polling entirely). Polling
runs regardless of whether the sidebar is visible, and stops while signed out.

Each run newly seen awaiting approval raises a notification: `TDT: <workspace>
<command> is awaiting approval (+add ~change -destroy).` with **Approve…**,
**Reject…** and **Open** actions — each delegates to the same command the
sidebar's context menu uses (Approve still goes through the confirmation
modal; nothing is applied from the notification click alone). A run is
notified at most once per 24 hours: the set of already-notified run ids is
kept in extension global state, so it survives a window reload. Each VS Code
window polls and notifies independently, and a run still awaiting approval
24 hours later is announced once more — an intended reminder, not a duplicate.

A plan you start from VS Code is announced by the run output's own "awaiting
approval" toast only: the run is marked as seen the moment that toast goes up,
so the background poll does not raise a second notification for it.

On sign-in (or switching business unit) nothing fires for runs that are
already awaiting approval at that moment — they're recorded as seen
immediately so you aren't sprayed with a backlog of notifications; the Runs
view's badge count still reflects them. Only runs that newly enter
`awaiting_approval` afterwards produce a notification.

Setting: `terraducktel.approvals.pollSeconds` (default 60, 0 disables).

## Editor integration

While editing a `.tf`, `.tfvars`, or `.hcl` file, a status bar item shows the
mapped workspace (if any): `$(cloud) TDT: <name> · <last run status>`. The
background is the theme's error status-bar background after a failed run,
the warning background while awaiting approval, and default otherwise. When
the file has no workspace, the item shows `TDT: not imported` with an action
to open the Discover flow.

Clicking the status bar item opens a quick pick:
- **Plan this leaf** — trigger a plan on the file's workspace. If the current
  git branch differs from the workspace's tracked ref, you can choose to plan on
  the current branch (which pins the workspace's `repo_ref` via `PUT
  /workspaces/{id}`), or on the tracked ref without pinning.
- **Show last plan** — open the plan output (if a run exists).
- **Reveal in sidebar** — jump to this workspace in the Workspaces tree.
- **Open in browser** — opens the Terraducktel dashboard (the web UI has no
  per-workspace page to deep-link to).

**Commands:**
- `Terraducktel: Current file: actions…` (`terraducktel.currentFileActions`)
  — palette only; shows the same quick pick as clicking the status bar.
- `Terraducktel: Plan this leaf` (`terraducktel.planCurrentFile`)
  — palette and editor-title button (shown when the file is mapped and you have
  write access).
- `Terraducktel: Reveal current workspace in sidebar`
  (`terraducktel.revealCurrentWorkspace`) — palette only.

**How a file is matched:**
The editor resolves the workspace whose `repo_url` matches the checkout's
`origin` (scheme, user, `.git` suffix, and case are ignored) **and** whose
`tf_working_dir` is the longest prefix of the file's directory relative to the
git root. `local://` workspaces match by `tf_working_dir` alone. When the folder
has no git remote or the remote is not recognised, the editor falls back to
matching by `tf_working_dir` alone, but only if exactly one workspace fits.
Whole-repo workspaces with `tf_working_dir="."` are never matched.

**Git information:**
Git information comes from `git` on your PATH (`rev-parse --show-toplevel`,
`remote get-url origin`, and `rev-parse --abbrev-ref HEAD`), is cached for 10
seconds per checkout, and times out after 3 seconds — the editor never blocks
waiting for git. Mapping works only inside a git checkout.

The `terraducktel.statusBar.enabled` setting (default true) toggles the status
bar item globally.

A second, compact status bar item (left of the current-file one) shows the
active profile — `$(server) <profile>`, plus ` · <bu>` once signed in with a
business unit set. Click it to run **Terraducktel: Switch profile**. It's
hidden entirely until at least one profile exists.

## Development

`npm run watch` + F5 (Extension Development Host). `npm test` runs the unit
suite against an in-process fake API; `npm run test:integration` runs a
headless VS Code smoke against a stub server. Every endpoint the extension
calls is listed in `api_contract.json` and guarded by
`services/api/tests/test_vscode_api_contract.py`.
