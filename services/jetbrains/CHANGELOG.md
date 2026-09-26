# Changelog

All notable changes to the Terraducktel JetBrains plugin are documented here.

## Unreleased

Brand redesign. Every action, gate and API call is unchanged; only how things
look and where run output opens.

- **Stacked sections**: the Terraducktel tool window shows Workspaces and Runs
  as two stacked, collapsible sections (like the VS Code sidebar) instead of
  tabs. Collapsed state is remembered per project; the Runs header shows the
  awaiting-approval count as a pill and the stripe icon gets a live indicator
  while any run awaits approval.
- **Terraducktel Run** tool window (bottom): run consoles moved here, one
  closeable `Run <id8> · <workspace>` tab per watched run; Plan / Apply /
  Destroy / Watch Run open and activate it.
- Brand status icons (light/dark SVGs) in both trees, the platform's animated
  spinner for in-flight runs, and provider glyphs in the brand accent. Row text
  now matches VS Code (`<leaf>  <status> · <branch>[ · drift]`, run
  `status · branch · id8 · local time`, step durations).
- All brand colours live in `TdtColors`; the plan document and console header
  colours are colour-scheme keys, editable under Settings → Editor → Color
  Scheme → Terraducktel.
- The plan document uses brand diff colours with a separate colour for
  replaced (`-/+`) lines, plus a 2px gutter bar.
- Console `── step [status]` headers are bold and tinted by status.
- **Approve…** dialog: `Approve <command> on <workspace>?`, the verbose summary
  (replace omitted when 0), then "Nothing is applied until you click
  Approve.", with Approve / Show plan / Cancel.
- Approval balloons are titled **Terraducktel approvals** and use an HTML body
  (sentence, line break, summary) so they wrap.

## 0.1.0

- **Sign-in**: email + password, a long-lived API key (`tdt_…`), or SSO via a
  `127.0.0.1` loopback callback (same flow as `tdt login --sso`; unavailable
  when the IDE is a remote-dev host — use an API key there). Credentials are
  stored per profile in the IDE's PasswordSafe, never in the settings XML;
  access tokens are refreshed automatically on 401.
- **Settings**: a Settings → Tools → Terraducktel page with a profiles table
  (name, API URL, UI URL, insecure TLS), an active-profile selector, refresh
  interval, runs limit, approval poll interval, a status-bar-item toggle, and
  trace-logging toggle. Renaming or removing a profile migrates or deletes
  its stored credential and remembered business unit.
- **Tool window**: a Workspaces tree (provider → region → folders →
  workspace, with drift and last-run status) and a Runs tree
  (awaiting-approval first, then in-flight, then landed), sharing a toolbar
  with Sign In/Out, Switch Profile, Switch Business Unit, Refresh, and Watch
  Run. The Runs tab title shows a live `Runs · N` count of runs awaiting
  approval.
- **Runs**: Plan / Apply… (with confirmation) / Destroy… (type-the-name
  guard) from the Workspaces tree; Set Tracked Branch… and Sync From Repo.
  Every trigger opens a console tab that tails the run's steps live;
  reopening the same run's Watch Run… resumes tailing where it left off. A
  run that lands `awaiting_approval` or `failed` while being watched raises
  a balloon.
- **Approvals**: Approve… loads the plan's graph summary
  (`+add ~change -destroy ±replace`) into a three-way dialog (Approve / Show
  plan / Cancel) — nothing is applied without an explicit Approve click.
  Reject… prompts for an optional reason. Cancel Run requests cancellation of
  a still-cancellable run. A run's plan opens as a read-only, diff-coloured
  document (Show Plan).
- **Current file → workspace**: a status-bar item maps the active
  `.tf`/`.tfvars`/`.hcl` file to its imported workspace (longest matching
  `tf_working_dir` prefix, git-remote-aware, falling back to path-only
  matching for an unknown remote; a `tf_working_dir` of `.` never matches).
  Click it — or **Tools → Terraducktel → Terraducktel Actions for Current
  File**, also on the editor's right-click menu — for Plan this leaf (offers
  to pin the workspace's tracked branch when the checked-out branch
  differs), Show last plan, Reveal in tool window, and Open in browser. A
  second, smaller item shows the active profile and business unit.
- **Approval notifications**: while signed in, a background poll (Settings →
  Tools → Terraducktel, default 60s, floor 15s, 0 disables it) checks for
  runs newly awaiting approval in the active business unit and raises a
  sticky balloon for each — `+add ~change -destroy ±replace` counts and
  Approve…/Reject…/Open actions, all going through the same gated flow as
  the Runs tree. Each run is announced at most once per 24 hours (tracked
  across restarts); runs already awaiting approval at sign-in, and any run
  this window is actively watching, are never double-announced.
- Every endpoint the plugin calls is pinned in `api_contract.json` and
  guarded by `services/api/tests/test_jetbrains_api_contract.py`.
