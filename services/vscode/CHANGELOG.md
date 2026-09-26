# Changelog

All notable changes to the Terraducktel VS Code extension are documented here.

## Unreleased

Brand redesign. Every command, gate and API call is unchanged; only how things look.

- Status icons in the Workspaces and Runs trees use the Terraducktel icon set
  in brand colours (light/dark SVGs under `media/status/`); in-flight runs and
  steps spin in the new `terraducktel.run` colour, and cloud-group icons are
  tinted `terraducktel.accent`.
- New theme colours `terraducktel.add`, `.change`, `.destroy`, `.replace`,
  their `*Background`s, `.run` and `.accent` — overridable via
  `workbench.colorCustomizations`.
- The plan document paints `+ ~ - -/+` lines with the brand diff colours
  (background, text and overview-ruler mark; replace lines get a 2px left bar).
- Run output channels use the new `terraducktel-output` language: its grammar
  gives `── step [status]` headers theme scopes by status, so they are tinted
  by your theme.
- **Approve…** is now a modal information dialog: `Approve <command> on
  <workspace>?` with the full `+N to add, ~N to change, -N to destroy, ±N to
  replace` summary as its detail (replace omitted when 0).
- The awaiting-approval badge moved from the Runs view to the Workspaces view
  (`N awaiting approval`).

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
