# Changelog

All notable changes to the Terraducktel VS Code extension are documented here.

## 0.3.1

- Profiles are now editable natively in the Settings UI: `terraducktel.profiles`
  and `terraducktel.uiUrls` are name→url maps, `terraducktel.insecureTlsProfiles`
  is a plain string array — no more hand-edited JSON array in `settings.json`.
  The legacy array form is still read for backward compatibility.
- New **Terraducktel: Add profile…** and **Terraducktel: Remove profile…**
  commands (input boxes + quick picks; no JSON editing required).
- The active profile now lives in extension global state, set via
  **Terraducktel: Switch profile** (sidebar title button or status bar item);
  the old `terraducktel.activeProfile` setting is deprecated and only read
  once, to migrate an existing value.
- New compact status bar item showing the active profile (and business unit,
  once signed in); click it to switch profiles.
- The legacy per-profile `bu` default (no home in the new map schema) is
  migrated into the same per-profile business-unit memory **Switch business
  unit** already uses, the first time a profile add/remove rewrites settings
  — it is never silently dropped.
- Brand icon for the Extensions view and marketplace, and a redrawn
  monochrome activity-bar mark.
- Real README, this changelog, and marketplace gallery metadata (keywords,
  homepage, banner colour).

## 0.3.0

- Approval notifications: polls `GET /runs?status=awaiting_approval` for the
  active business unit and raises a notification for each run newly awaiting
  approval, with Approve…/Reject…/Open actions.
- Per-run notification dedupe persisted in extension global state for 24
  hours, so a reload doesn't repeat a notification the user already saw.
- Hardened the approval poll loop and rearm logic against sign-out races,
  priming failures, and epoch mismatches from an in-flight tick.
- Fixed symlinked-checkout path resolution and a stale-branch race when
  pinning a workspace's tracked branch from **Plan this leaf**.

## 0.2.0

- Editor integration: a status bar item shows the active `.tf`/`.tfvars`/
  `.hcl` file's mapped Terraducktel workspace and its last run status.
- **Plan this leaf** command, with a branch-pin quick pick when the checked-
  out branch differs from the workspace's tracked ref.
- Pure editor → workspace mapping (repo URL normalisation, longest
  `tf_working_dir` prefix match) backed by a cached git probe (root, remote,
  branch).
- **Reveal current workspace in sidebar** command.

## 0.1.0

- Initial scaffold: typed Terraducktel API client with refresh-on-401.
- Profiles, SSO loopback sign-in, and per-profile credential storage in VS
  Code `SecretStorage`.
- Workspaces and Runs sidebar tree views backed by a polling store, grouped
  like the web UI.
- Run tailing, a diff-coloured plan document, and gated approve/reject.
- Session/auth lifecycle hardening and a headless integration smoke test.
