# Changelog

All notable changes to the Terraducktel JetBrains plugin are documented here.

## 0.1.0

- **Sign-in**: email + password, a long-lived API key (`tdt_…`), or SSO via a
  `127.0.0.1` loopback callback (same flow as `tdt login --sso`; unavailable
  when the IDE is a remote-dev host — use an API key there). Credentials are
  stored per profile in the IDE's PasswordSafe, never in the settings XML;
  access tokens are refreshed automatically on 401.
- **Settings**: a Settings → Tools → Terraducktel page with a profiles table
  (name, API URL, UI URL, insecure TLS), an active-profile selector, refresh
  interval, runs limit, and trace-logging toggle. Renaming or removing a
  profile migrates or deletes its stored credential and remembered business
  unit.
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
- Every endpoint the plugin calls is pinned in `api_contract.json` and
  guarded by `services/api/tests/test_jetbrains_api_contract.py`.
