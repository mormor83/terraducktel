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

**User settings only.** `profiles`, `uiUrls`, `insecureTlsProfiles` and the
legacy `activeProfile` decide where your stored credential is sent, so they
are `application`-scoped: values in a repository's `.vscode/settings.json` or
a `.code-workspace` file are ignored (the extension logs which ones it
skipped to the *Terraducktel* output channel). On top of that, every stored
credential is bound to the API URL it was issued for: if a profile's URL is
later changed, the old credential is not sent to the new URL — you are asked
to sign in again (switching the URL back restores the old session).

A profile that points at a **plain `http://`** URL on anything other than
`localhost` / `127.0.0.0/8` / `[::1]` triggers a one-time warning, since your
password and tokens would cross the network unencrypted. It is a warning,
not a block — use `https://` unless it is a trusted private network.

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

There is no business-unit setting: the extension shows every business unit
you can access (see **Business units** below). The old per-profile `bu` field
and the remembered "active business unit" are no longer used; a `bu` in a
legacy profile array is still parsed but ignored.

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

- **Workspaces** view: business unit → provider → account → region → folders →
  workspace, each with drift and last-run status; expand a workspace for its
  recent runs. Right-click for Plan / Apply… / Destroy… / Set tracked branch / Sync from
  repo / Open in browser / Copy id. The view's badge (and so the activity-bar
  icon) counts runs awaiting approval.
- **Runs** view: grouped by business unit; within each, awaiting-approval on
  top, then in-flight, then landed; newest first within a status. A business
  unit with no runs shows "No recent runs".
  Click a run to watch its steps in an output channel, or expand it to see its
  steps with status icons and durations. Inline icons: watch, plan output and
  (for a run awaiting approval) Approve…; **Reject…** and **Cancel run** are on
  the right-click menu.
- **Approve…** asks `Approve <command> on <workspace>?` in a modal dialog whose
  detail is the plan graph summary — `+2 to add, ~1 to change, -2 to destroy,
  ±1 to replace` (the replace count is left out when it is 0) — with
  **Approve**, **Show plan** and Cancel. Nothing is applied without an
  explicit **Approve** click; **Destroy…** asks you to type the workspace name.
- Title-bar buttons: switch profile (only shown once at least one profile
  exists), refresh, filter business units.

Settings: `refreshIntervalSeconds` (default 30), `runsLimit` (200), `trace`
(request metadata to the *Terraducktel* output channel; never credentials).

### Business units

Business units are the top level of both trees:

```
▾ Payments            payments · 12 workspaces
  ▾ AWS · 123456789012
    ▾ eu-west-1
      ▸ vpc
▸ Platform            platform · 4 workspaces
▸ Data                data · 7 workspaces
```

- Every business unit you can access is listed, sorted by name — all of them
  for a superadmin, your memberships for everyone else. Each row reads
  `slug · N workspaces` (or `slug · error` if that business unit failed to
  load). Under it is the usual provider → account → region → folders →
  workspace grouping, built from that business unit's workspaces only. An
  empty business unit shows "No workspaces".
- If exactly one business unit is visible it starts expanded; otherwise they
  start collapsed, and the extension remembers what you expand.
- A business unit that fails to load shows its error on its own row; the other
  business units keep their last good data. A business unit you lose access to
  disappears from the tree on the next refresh.
- **Terraducktel: Filter business units…**
  replaces the old *Switch business unit* command (removed). It opens a
  multi-select list with the visible business units checked; uncheck the ones
  you don't want. You can't hide all of them.
  It is also in the Workspaces view's title bar. While some are hidden, the
  first row of the Workspaces tree reads `Showing X of Y business units —
  Filter…`; click it to reopen the picker.
- The filter is stored **per profile** as the list of *hidden* business units,
  so a business unit you've never hidden — including one you're newly added to —
  is visible by default. It applies to the Workspaces view, the Runs view, and
  approval notifications alike.
- Every action on a workspace or run (plan, apply, destroy, approve, reject,
  open in browser, show plan, …) is sent with **that workspace's own business
  unit**, regardless of which others are visible. If you lack the role in that
  business unit (e.g. a viewer) the server's message is shown. Write actions are
  never hidden on the client based on role — they only require being signed in.
- The web UI keeps its business-unit selection in the browser, so **Open in
  browser** opens the run in whatever business unit the web UI currently has
  selected; if the page says not found, switch business unit there.
- **Request load scales with the number of visible business units**: each
  refresh fetches workspaces and runs once per visible business unit, and the
  approval poll fetches awaiting runs once per visible business unit. Each of
  the two keeps at most 4 HTTP requests in flight. If you belong to many,
  filter down to the ones you work in.

### Look and colours

- **Status icons** in both trees come from the Terraducktel icon set, coloured
  per status and per light/dark theme (`media/status/<status>-{light,dark}.svg`):
  applied/success ✓ green, planned ✓ cyan, awaiting approval ⏸ amber, failed ✕
  red, cancelled/skipped ■ and pending ◷ muted, a workspace with no runs gets
  the workspace glyph. Runs and steps still in flight show a spinning sync
  icon in the `terraducktel.run` colour. Cloud-group rows are tinted with
  `terraducktel.accent`.
- **Plan document** lines are painted with brand diff colours — added,
  changed, destroyed and replaced (`-/+`, plus a 2px left bar) — as whole-line
  background, text colour and an overview-ruler mark.
- **Run output**: the output channel uses the `terraducktel-output` language,
  whose grammar gives `── step [status]` / `── run <status>` header lines
  theme scopes by status (`markup.inserted` for success/applied/planned,
  `markup.deleted` for failed/cancelled, `markup.changed` for awaiting
  approval, `comment` for skipped, all under `markup.heading`), and `✕` error
  lines `markup.deleted`. Your colour theme decides the exact tint and whether
  headers are bold; the text itself is unchanged.
- Every colour is a `terraducktel.*` theme colour (`add`, `change`, `destroy`,
  `replace`, the matching `*Background`s, `run`, `accent`). Override any of
  them under `workbench.colorCustomizations`, e.g.
  `"workbench.colorCustomizations": { "terraducktel.addBackground": "#00ff0022" }`.

## Approval notifications

While signed in, the extension polls `GET /runs?status=awaiting_approval` for
every visible business unit every `terraducktel.approvals.pollSeconds` (default
60; values below 15 are treated as 15; 0 disables polling entirely). Polling
runs regardless of whether the sidebar is visible, and stops while signed out.

Each run newly seen awaiting approval raises a notification: `TDT: <workspace>
(<business unit>) <command> is awaiting approval (+add ~change -destroy).` with **Approve…**,
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

On sign-in, a profile change, or a change to the business-unit filter (hiding
or showing business units) nothing fires for runs that are
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
  — palette and editor-title button, available whenever you're signed in and the
  file is mapped. The server enforces your per-business-unit role; if it refuses
  (403) its message is shown.
- `Terraducktel: Reveal current workspace in sidebar`
  (`terraducktel.revealCurrentWorkspace`) — palette only.

**How a file is matched:**
The editor looks across every loaded business unit and resolves the workspace whose `repo_url` matches the checkout's
`origin` (scheme, user, `.git` suffix, and case are ignored) **and** whose
`tf_working_dir` is the longest prefix of the file's directory relative to the
git root. `local://` workspaces match by `tf_working_dir` alone. When the folder
has no git remote or the remote is not recognised, the editor falls back to
matching by `tf_working_dir` alone, but only if exactly one workspace fits.
Whole-repo workspaces with `tf_working_dir="."` are never matched. The match is made per business unit; if
more than one business unit has a match (even at different path depths), a quick pick lists
each one's best match, sorted by business unit name, as `workspace — business unit` and you choose which one the action is for.

**Git information:**
Git information comes from `git` on your PATH (`rev-parse --show-toplevel`,
`remote get-url origin`, and `rev-parse --abbrev-ref HEAD`), is cached for 10
seconds per checkout, and times out after 3 seconds — the editor never blocks
waiting for git. Mapping works only inside a git checkout.

The `terraducktel.statusBar.enabled` setting (default true) toggles the status
bar item globally.

A second, compact status bar item (left of the current-file one) shows the
active profile — `$(server) <profile>`, plus ` · X/Y BUs` once signed in
(X visible out of Y accessible business units). Click it to run **Terraducktel: Switch profile**. It's
hidden entirely until at least one profile exists.

## Development

`npm run watch` + F5 (Extension Development Host). `npm test` runs the unit
suite against an in-process fake API; `npm run test:integration` runs a
headless VS Code smoke against a stub server. Every endpoint the extension
calls is listed in `api_contract.json` and guarded by
`services/api/tests/test_vscode_api_contract.py`.
