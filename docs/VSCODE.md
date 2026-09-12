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
  Click a run to watch its steps in an output channel; use the inline icons
  for plan output, Approve…, Reject.
- **Approve…** shows `+add ~change -destroy ±replace` from the plan graph and
  needs an explicit click; **Destroy…** asks you to type the workspace name.
- Title-bar buttons: refresh, switch business unit.

Settings: `refreshIntervalSeconds` (default 30), `runsLimit` (200), `trace`
(request metadata to the *Terraducktel* output channel; never credentials).

## Development

`npm run watch` + F5 (Extension Development Host). `npm test` runs the unit
suite against an in-process fake API; `npm run test:integration` runs a
headless VS Code smoke against a stub server. Every endpoint the extension
calls is listed in `api_contract.json` and guarded by
`services/api/tests/test_vscode_api_contract.py`.
