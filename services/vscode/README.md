<p align="center"><img src="media/icon.png" width="96" height="96" alt="Terraducktel"></p>

# Terraducktel for VS Code

Terraducktel (TDT) is a self-hosted Terraform (and Helm) orchestration
platform: `plan → checkov → cost-estimate → human approval → apply`, gated,
audited, multi-tenant. This extension drives a Terraducktel deployment from
inside VS Code — browse workspaces and runs, trigger and watch plans/applies,
review the plan as a diff-coloured document, and approve or reject from a
notification, without leaving the editor.

## Features

- **Workspaces & Runs sidebar** — a Workspaces tree grouped like the web UI
  (provider → account → region → folders → workspace) with drift and
  last-run status, and a Runs tree (newest first, awaiting-approval on top
  with a badge count).
- **Plan / Apply… / Destroy…, with live steps** — trigger a run from the
  sidebar or the command palette and watch its steps stream into an output
  channel as they complete.
- **Plan diff document** — the plan output opens as a read-only, diff-coloured
  document instead of a wall of JSON.
- **Gated approvals** — Approve… shows `+add ~change -destroy ±replace` from
  the plan graph and needs an explicit click; Reject… and Cancel run are one
  click away.
- **Editor status bar + plan-this-leaf** — while editing a `.tf`/`.tfvars`/
  `.hcl` file, a status bar item shows the mapped workspace and its last run
  status; click it (or run **Plan this leaf**) to plan straight from the
  file, with branch-pinning if your checkout is on a different branch than
  the workspace tracks.
- **Approval notifications** — polls for runs newly awaiting approval in the
  active business unit and raises a notification with Approve…/Reject…/Open
  actions.

## Requirements

- A Terraducktel API the extension can reach (self-hosted; there is no
  hosted/SaaS instance).
- A sign-in mode the server supports: email + password, a long-lived API key
  (`tdt_…`), or SSO via the server's browser loopback hand-off. SSO is not
  available over Remote-SSH — use an API key there instead.

## Getting started

1. Add a profile: run **Terraducktel: Add profile…** from the command
   palette (name, API origin, optional web UI origin, TLS setting), or add
   rows by hand under **Settings → Extensions → Terraducktel**.
2. Run **Terraducktel: Sign in** and pick a sign-in mode.
3. Open the Terraducktel icon in the activity bar to browse workspaces and
   runs.

See `docs/VSCODE.md` in the repository for the full setup and usage guide.

## Settings

| Setting | Default | |
|---|---|---|
| `terraducktel.profiles` | `{}` | Profile name → API origin (e.g. `prod` → `https://tdt.example.com`). Editable as rows in the Settings UI. |
| `terraducktel.uiUrls` | `{}` | Profile name → web UI origin, only when it differs from the API origin. |
| `terraducktel.insecureTlsProfiles` | `[]` | Profile names for which TLS certificate verification is skipped (self-signed dev stacks only). |
| `terraducktel.activeProfile` | `""` | Deprecated — the active profile is now chosen with **Switch profile**; this is read once to migrate a pre-0.3.1 value. |
| `terraducktel.refreshIntervalSeconds` | `30` | How often the sidebar polls while visible. |
| `terraducktel.runsLimit` | `200` | How many recent runs to fetch. |
| `terraducktel.trace` | `false` | Log request/response metadata (never credentials) to the *Terraducktel* output channel. |
| `terraducktel.statusBar.enabled` | `true` | Show the current file's mapped workspace in the status bar. |
| `terraducktel.approvals.pollSeconds` | `60` | How often to poll for runs newly awaiting approval (seconds); `0` disables. |

## Commands

| Command | Title |
|---|---|
| `terraducktel.signIn` | Sign in |
| `terraducktel.signOut` | Sign out |
| `terraducktel.switchProfile` | Switch profile |
| `terraducktel.addProfile` | Add profile… |
| `terraducktel.removeProfile` | Remove profile… |
| `terraducktel.switchBusinessUnit` | Switch business unit |
| `terraducktel.refresh` | Refresh |
| `terraducktel.plan` | Plan |
| `terraducktel.apply` | Apply… |
| `terraducktel.destroy` | Destroy… |
| `terraducktel.setBranch` | Set tracked branch… |
| `terraducktel.syncWorkspace` | Sync from repo |
| `terraducktel.openInBrowser` | Open in browser |
| `terraducktel.copyId` | Copy id |
| `terraducktel.watchRun` | Watch run (show steps) |
| `terraducktel.showPlan` | Show plan output |
| `terraducktel.approve` | Approve… |
| `terraducktel.reject` | Reject… |
| `terraducktel.cancelRun` | Cancel run |
| `terraducktel.currentFileActions` | Current file: actions… |
| `terraducktel.planCurrentFile` | Plan this leaf |
| `terraducktel.revealCurrentWorkspace` | Reveal current workspace in sidebar |

## Security notes

- Credentials (refresh token or API key) are stored one per profile in VS
  Code's `SecretStorage`; access tokens live in memory only. Nothing secret
  is ever written to settings, logs, or the output channel.
- No telemetry of any kind is collected or sent by this extension.
- `terraducktel.insecureTlsProfiles` skips TLS certificate verification for
  the named profiles — only use it against a self-signed dev stack you
  control, never a real deployment.

## Known limitations

- SSO sign-in needs a browser on the same machine as the listening loopback
  server, so it does not work over Remote-SSH — use an API key there.
- The web UI has no per-workspace deep link, so "Open in browser" always
  opens the dashboard rather than the specific workspace.
- Editor-file → workspace mapping requires a git checkout; it does not work
  outside one.

See `docs/VSCODE.md` for the full list and other implementation details.

## Release notes

See [`CHANGELOG.md`](./CHANGELOG.md).

---

Build from source: `npm run build`. Package: `npm run package`. Full
development and architecture docs: `docs/VSCODE.md` in the repository.
