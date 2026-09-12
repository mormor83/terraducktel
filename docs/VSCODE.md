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

Settings → Terraducktel → Profiles (or `settings.json`):

```json
"terraducktel.profiles": [
  { "name": "local", "url": "http://localhost:8001", "uiUrl": "http://localhost:3001", "bu": "default" },
  { "name": "prod",  "url": "https://tdt.example.com", "bu": "platform" }
],
"terraducktel.activeProfile": "local"
```

`url` is the API origin; `uiUrl` is only needed when the web UI lives on a
different origin (as in the dev compose stack). `insecureTls: true` skips
certificate verification for a self-signed dev stack and shows a warning in
the tree while active.

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
- Title-bar buttons: refresh, switch business unit.

Settings: `refreshIntervalSeconds` (default 30), `runsLimit` (200), `trace`
(request metadata to the *Terraducktel* output channel; never credentials).

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

## Development

`npm run watch` + F5 (Extension Development Host). `npm test` runs the unit
suite against an in-process fake API; `npm run test:integration` runs a
headless VS Code smoke against a stub server. Every endpoint the extension
calls is listed in `api_contract.json` and guarded by
`services/api/tests/test_vscode_api_contract.py`.
