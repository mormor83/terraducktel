# Changelog

All notable changes to the Terraducktel VS Code extension are documented here.

## Unreleased

Business units are now the top level of the sidebar. Every business unit you can
access is a root node in both the Workspaces and Runs trees (its workspaces
keep the provider → account → region → folders grouping beneath it), instead
of one "current" BU you had to switch between.

- **Terraducktel: Filter business units…** (sidebar title button, the
  "Showing X of Y business units" row, or the command palette) replaces
  *Switch business unit*. It is a multi-select; the choice is remembered per
  profile as the BUs you hid, so BUs added later show up by default, and at
  least one must stay selected.
- Each poll fetches workspaces and runs once per visible BU (with that BU's
  `X-Business-Unit`, up to 4 in parallel). One BU failing shows an error on
  that BU only; the others keep their data.
- Every action on a workspace or run (plan/apply/destroy, approve/reject/
  cancel, sync, branch, plan output) uses the BU of the node it was started
  from. From the command palette or the editor status bar, the pickers span all
  visible BUs; a file whose path is imported in several BUs asks which
  workspace you mean ("workspace — BU").
- Approval notifications watch all visible BUs and name the BU in the message.
  Changing the filter re-primes them, so newly shown BUs do not announce their
  whole backlog.
- The status bar shows `profile · X/Y BUs`.
- Removed: the per-profile active-BU state (`bu.<profile>` in workspace/global
  state) and the legacy `bu` field of array-form profiles are no longer used.

Security: a repository could redirect your credentials. `terraducktel.profiles`,
`uiUrls`, `insecureTlsProfiles` and `activeProfile` were window-scoped, so a
cloned repo's `.vscode/settings.json` could re-point an existing profile (say
`prod`) at its own host, and the extension would send that profile's stored
refresh token / API key there. Now:

- those settings are `application`-scoped and restricted in untrusted
  workspaces, and the extension reads them only from User settings, ignoring
  workspace / folder values even where VS Code would still merge them;
- stored credentials record the API URL they were issued for and are never
  sent to a different URL — change a profile's URL and you sign in again.
  Credentials stored by earlier versions are bound to the profile's current
  URL the first time they are read.

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
