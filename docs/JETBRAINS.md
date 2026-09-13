# Terraducktel for JetBrains IDEs

Drive TDT from a JetBrains IDE (2026.1+): a Workspaces tree grouped like the
web UI, a Runs tree, plan/apply/destroy with a live console tail, the plan as
a diff-coloured document, and gated approvals.

## Install

Build the plugin zip and install it from disk:

```bash
make build-jetbrains
```

Then in the IDE: **Settings → Plugins → ⚙ (gear icon) → Install Plugin from
Disk…**, pick `services/jetbrains/build/distributions/terraducktel-jetbrains-<version>.zip`,
and restart the IDE when prompted.

(There is no Marketplace listing — installing from disk is the only
supported path for this release.)

## Configure a profile

**Settings → Tools → Terraducktel** has a profiles table (name, API URL, UI
URL, insecure TLS) plus general preferences:

- **Name** — must be unique; used to key the stored credential.
- **API URL** — the Terraducktel API origin, e.g. `http://localhost:8001`.
- **UI URL** — the web UI origin, only needed when it differs from the API
  origin (as in the dev compose stack, e.g. `http://localhost:3001`); falls
  back to the API URL when left blank.
- **Insecure TLS** — skip certificate verification for this profile
  (self-signed dev stacks only — never for a real deployment).
- **Active profile** — a dropdown below the table; this is the profile every
  action, tree, and sign-in uses.
- **Refresh interval (seconds)** — how often the tool window polls while
  visible (default 30).
- **Runs limit** — how many recent runs to fetch (default 200).
- **Approval poll seconds (0 = off)** — reserved for a future release: the
  setting is stored and editable here, but nothing in this build polls on
  it or raises approval notifications in the background. The only
  "awaiting approval" balloon you'll see today is for a run you started (or
  are actively watching) in this IDE session — see **Reviewing and
  approving** below.
- **Trace requests** — logs request/response metadata (never credentials) to
  `idea.log` under the `#com.terraducktel` logger.

Renaming a profile in the table migrates its stored credential and
remembered business unit to the new name; removing a profile deletes its
stored credential. Nothing secret ever appears in this table or in
`terraducktel.xml` — credentials live in the IDE's PasswordSafe, keyed
`terraducktel.cred.<profile name>`.

## Sign in

**Tools → Terraducktel → Sign In…** (or the tool window toolbar's Sign In
action). The plugin asks the server which providers exist and offers:

| Mode | Notes |
|---|---|
| SSO | Opens your browser; the server hands the tokens back on `127.0.0.1` (same flow as `tdt login --sso`). Not available when the IDE is running as a remote-dev host (JetBrains Gateway / Remote Development) — use an API key there instead. |
| Email + password | Stores only the refresh token; renews itself. |
| API key | Paste a `tdt_…` key minted in the UI. Bound to one business unit. |

Credentials live in the IDE's PasswordSafe, never in the settings XML or in
`idea.log`.

## The tool window

Open the **Terraducktel** tool window (left-hand tool window bar) for two
tabs:

- **Workspaces** — grouped like the web UI: provider (AWS account / Azure
  subscription / GCP project / other) → region → folders → workspace, each
  row showing drift and last-run status; expand a workspace to see its
  recent runs as child rows.
- **Runs** — every run in the current business unit, flat, most-actionable
  first: `awaiting_approval` on top, then in-flight (`pending` / `running` /
  `planning` / `applying`), then landed (`planned` / `applied` / `failed` /
  `cancelled`). The tab title becomes **Runs · N** while N runs are awaiting
  approval.

The toolbar (shared across both tabs) has Sign In…, Sign Out, Switch
Profile…, Switch Business Unit…, Refresh, and Watch Run…. Right-click a
workspace row for its context menu (Plan / Apply… / Destroy… / Set Tracked
Branch… / Sync From Repo / Open in Browser / Copy Id); right-click a run row
for its own (Show Plan / Approve… / Reject… / Cancel Run / Watch Run… / Open
in Browser / Copy Id).

## Triggering runs

- **Plan** — triggers a plan immediately and opens a console tab for it.
- **Apply…** — asks for confirmation ("The plan will pause for approval
  before anything changes.") before triggering.
- **Destroy…** — asks you to type the workspace's exact name before
  triggering; anything else cancels.
- **Set Tracked Branch…** — lists the repo's branches (current one marked) in
  a popup, with an "Other…" entry to type a free-form ref; picking a
  different branch pins the workspace's `repo_ref` before the next run.
- **Sync From Repo** — re-syncs the workspace against its source repo.

Every trigger opens (or reveals) a console tab in the tool window titled
`Run <short id> · <workspace>` and streams its steps live. Closing that tab
stops following the run (the request already sent to the server is
unaffected); reopening **Watch Run…** on the same run resumes tailing from
where it left off, marked with a `re-attached` separator. When a run you're
watching lands as `awaiting_approval` or `failed`, a balloon appears; a
`failed` run just links back to the console output, an `awaiting_approval`
one offers **Show plan** and **Approve…** directly from the balloon.

## Reviewing and approving

- **Show Plan** opens the run's `terraform plan` output as a read-only
  document with diff colouring: added lines highlighted as insertions,
  removed lines as deletions, changed/replaced lines as modifications.
- **Approve…** loads the plan's graph summary and shows `+N to add, ~N to
  change, -N to destroy, ±N to replace` in a three-way dialog: **Approve**
  applies, **Show plan** opens the plan document instead, **Cancel** does
  nothing. Nothing is ever applied without an explicit **Approve** click.
- **Reject…** prompts for an optional reason, then rejects.
- **Cancel Run** requests cancellation of a run that is still cancellable
  (queued or in-flight).

All of the above require write access (operator/admin in the active business
unit) — the actions are hidden entirely for a read-only session.

## Troubleshooting

- **Self-signed certificate errors** — check **Insecure TLS** for that
  profile in Settings → Tools → Terraducktel. Only do this against a dev
  stack you control.
- **"Not signed in to Terraducktel. Run 'Terraducktel: Sign in'."** — no
  active session for the active profile; run **Sign In…** again. This also
  shows up if the active profile was just changed or removed out from under
  a signed-in session.
- **Trace logging** — turn on **Trace requests** in Settings → Tools →
  Terraducktel, then reproduce the issue and open **Help → Show Log in
  Files/Finder** (or tail `idea.log` directly). Look for lines from the
  `#com.terraducktel` logger — request/response metadata only, never
  credentials.
- **Tool window not visible** — it may have been closed; reopen it from the
  left-hand tool window bar's stripe (right-click the bar → Terraducktel) or
  via **View → Tool Windows → Terraducktel**.

## Building from source

```bash
make test-jetbrains    # ./gradlew test, JDK auto-resolved (see below)
make build-jetbrains   # ./gradlew buildPlugin -> services/jetbrains/build/distributions/*.zip
```

Both targets need a JDK ≥ 17 on `JAVA_HOME` to run Gradle itself (Gradle then
auto-provisions the JDK 21 toolchain the plugin compiles against). If neither
`JAVA_HOME` nor a `java` on `PATH` is available, the Makefile falls back to a
Toolbox-installed JetBrains Runtime under
`~/.local/share/JetBrains/Toolbox/apps/*/jbr`.

Every endpoint the plugin calls is listed in
`services/jetbrains/api_contract.json` and guarded by
`services/api/tests/test_jetbrains_api_contract.py` — the same pattern as the
VS Code extension's contract (see [VSCODE](VSCODE.md)) and the `tdt` CLI's.
