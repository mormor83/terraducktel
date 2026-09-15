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
- **Approval poll seconds (0 = off)** — how often the plugin polls for runs
  awaiting approval in the background, independent of which IDE window or
  tool window is open; positive values below 15 are floored to 15, and `0`
  disables the poll entirely (default 60). See **Approval notifications**
  below.
- **Show status bar item** — a status-bar item next to the caret position
  showing which Terraducktel workspace the active `.tf`/`.tfvars`/`.hcl` file
  maps to; click it (or **Tools → Terraducktel → Terraducktel Actions for
  Current File**) for a plan/show-last-plan/reveal-in-tool-window/open-in-
  browser popup. A second, smaller item to its left shows the active profile
  (and BU, once signed in) — click to switch profiles.
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

## Current file → workspace

When **Show status bar item** is on (Settings → Tools → Terraducktel;
default on) and the active editor holds a `.tf`, `.tfvars`, or `.hcl` file,
a status-bar item next to the caret position shows which imported workspace
that file belongs to:

- It resolves the file's git root and remote (`git remote get-url origin`,
  cached for ~10 seconds), takes the file's directory relative to that
  root, and matches it against every known workspace's `tf_working_dir`,
  preferring the **longest matching prefix** when more than one workspace's
  directory contains the file (e.g. a leaf workspace nested under a region
  workspace). A workspace whose `tf_working_dir` is `.` (the repo root) is
  **never matched** — a root-level workspace would otherwise silently claim
  every file in every repo, which is never the intent.
- **Known remote**: the workspace's `repo_url` must resolve to the same
  host+path as the file's git remote (scheme, port, `.git` suffix, and case
  are normalized away first).
- **Unknown or missing remote** (no `origin`, or a remote the plugin can't
  parse): matching falls back to path alone. If exactly one workspace's
  `tf_working_dir` prefixes the file's path, that's the match; if more than
  one workspace ties on prefix length, the match is treated as ambiguous and
  nothing is shown — the plugin never guesses between two equally-plausible
  workspaces.
- Local (`local://`) checkouts always match by path alone, regardless of any
  git remote.
- When mapped, the item reads `TDT: <workspace>[ · <last run status>]`, with
  a tooltip giving the exact `tf_working_dir`, `(parent leaf)` when the match
  isn't the file's own directory, the checked-out branch when it differs
  from the workspace's tracked branch, and "Click for actions". When no
  workspace claims the file, it reads `TDT: not imported` instead — the item
  still shows (so there's always something to click), it just says nothing
  is imported here yet.
- A second, smaller item to its left shows the active profile (and business
  unit, once signed in); click it to switch profiles.

Click the item (or run **Tools → Terraducktel → Terraducktel Actions for
Current File**, also on the editor's right-click menu as **Plan This Leaf**
/ **Reveal Workspace**) for a popup:

- **Plan this leaf** — triggers a plan for the mapped workspace. If the
  file's checked-out branch differs from the workspace's tracked
  `repo_ref`, a choice appears first: **Plan on `<branch>` (pins the
  workspace)** re-points the workspace's tracked branch to the checked-out
  one before planning, or **Plan on `<repo_ref>`** plans on the
  already-tracked branch without changing anything. Nothing is pinned
  silently — the choice is always explicit.
- **Show last plan** (only offered when the workspace has a run) opens that
  run's plan document.
- **Reveal in tool window** expands and selects the workspace in the
  Workspaces tree.
- **Open in browser** opens the web UI's root — the only action offered
  when no workspace claims the file.

## Approval notifications

While signed in, the plugin polls `GET /runs?status=awaiting_approval` for
the active business unit every **Approval poll seconds** (Settings → Tools
→ Terraducktel; default 60; positive values below 15 are floored to 15;
`0` disables the poll entirely). Polling runs regardless of which tool
window is open, and stops while signed out.

Each run newly seen awaiting approval raises a sticky balloon (notification
group **Terraducktel approvals**): `TDT: <workspace> <command> awaits
approval`, with `+N to add, ~N to change, -N to destroy, ±N to replace`
when the graph summary is known, and **Approve…**, **Reject…**, **Open**
actions — each delegates to the same gated flow the Runs tree's context
menu uses (Approve still goes through the confirmation modal; nothing is
applied from the balloon click alone). A run is announced at most once per
24 hours: the set of already-announced run ids is kept in
`terraducktel.xml`, so it survives an IDE restart.

A run you started (or are actively watching) is announced by its own
"awaiting approval" balloon only (see **Triggering runs** above) — that run
is marked as seen the moment that balloon appears, so the background poll
never raises a second notification for it.

On sign-in (or a profile/business-unit switch) nothing fires for runs
already awaiting approval at that moment — they're recorded as seen
immediately so you aren't sprayed with a backlog; the Runs tab's `Runs · N`
badge still reflects them. Only runs that newly enter `awaiting_approval`
afterwards produce a notification.

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

## Building and verifying

```bash
make test-jetbrains     # ./gradlew test, JDK auto-resolved (see below)
make build-jetbrains    # ./gradlew buildPlugin -> services/jetbrains/build/distributions/*.zip
make verify-jetbrains   # ./gradlew verifyPlugin -> IntelliJ Plugin Verifier report
```

All three targets need a JDK ≥ 17 on `JAVA_HOME` to run Gradle itself (Gradle
then auto-provisions the JDK 21 toolchain the plugin compiles against). If
neither `JAVA_HOME` nor a `java` on `PATH` is available, the Makefile falls
back to a Toolbox-installed JetBrains Runtime under
`~/.local/share/JetBrains/Toolbox/apps/*/jbr`.

`verify-jetbrains` runs the IntelliJ Plugin Verifier against every IDE listed
in `build.gradle.kts`'s `intellijPlatform.pluginVerification.ides` block:
IntelliJ IDEA 2026.1 (pinned, matching `dependencies.intellijPlatform`) plus
whatever `recommended()` currently resolves to — several recent 2026.1/2026.2
builds JetBrains itself recommends verifying against. The first run downloads
each IDE distribution (multiple GB; cached under `~/.gradle/caches`
afterwards), so expect it to take several minutes and need network access the
first time.

`failureLevel` is set to only `COMPATIBILITY_PROBLEMS` (a call to an API that
doesn't exist in the target IDE) — everything else the verifier reports
(deprecated / experimental / internal API usage) is printed but doesn't fail
the build; the Gradle plugin's own default would fail on all of those, which
is stricter than this task needs. As of the 0.1.0 release the plugin is
compatible with every verified IDE with zero compatibility problems; the
verifier does flag 19 deprecated-API overrides (mostly platform interface
defaults like `ToolWindowFactory.isApplicable`/`isDoNotActivateOnStart` and
`StatusBarWidget.MultipleTextValuesPresentation.getMaxValue`), 6 experimental
API usages, and 2 internal API usages (`AppMode.isRemoteDevHost()` in
`SignInFlow`, used to hide SSO on a remote-dev host — guarded by a
try/catch that falls back to the `idea.is.remote.dev.host` system property
and `PlatformUtils.isJetBrainsClient()` if that internal method ever
disappears). CI runs `test`, `buildPlugin`, and
`verifyPluginProjectConfiguration` (a fast, static sanity check of
`plugin.xml`/`build.gradle.kts` — not the same task as `verifyPlugin`) on
every push and PR; `verifyPlugin` itself is a local/manual check given how
long it takes.

Every endpoint the plugin calls is listed in
`services/jetbrains/api_contract.json` and guarded by
`services/api/tests/test_jetbrains_api_contract.py` — the same pattern as the
VS Code extension's contract (see [VSCODE](VSCODE.md)) and the `tdt` CLI's.

## Differences from the VS Code extension

The two clients cover the same features against the same API, but four
things are deliberately different, each for a reason specific to the
IntelliJ Platform rather than an oversight:

1. **Profiles are a structured table, not a name→URL map.** VS Code's
   settings UI can only edit a flat JSON-ish map, so its profile store is
   shaped to fit that; the JetBrains Settings page can render a real table
   (name, API URL, UI URL, insecure TLS, an active-profile picker), so the
   plugin's `TdtSettings` stores profiles as a list instead. The behaviour
   is identical either way — this is a settings-UI shape difference, not a
   feature difference.
2. **The active business unit is remembered per profile, application-wide —
   not per project/window.** VS Code keeps a separate BU override per open
   window; the JetBrains plugin's session and polling store are
   application-level services shared by every open project, so the BU lives
   with the profile instead. Switching business unit switches it for every
   open project at once.
3. **Run output is a console tab in the Terraducktel tool window**, not a
   separate output panel — the IntelliJ Platform's console view
   (`ConsoleView`) is the idiomatic equivalent of VS Code's `OutputChannel`,
   and reusing the tool window keeps everything in one place.
4. **Network requests go through `HttpURLConnection`, not a shared HTTP
   client.** This lets each request's TLS trust be configured independently
   per connection, which is what makes the per-profile **Insecure TLS**
   toggle possible without affecting any other profile's requests.

Out of scope for both clients, for now: Marketplace publishing/signing,
editing workspace settings or creating/importing workspaces from inside the
IDE (both link out to the browser for that), and sharing credential storage
with the `tdt` CLI or with each other.
