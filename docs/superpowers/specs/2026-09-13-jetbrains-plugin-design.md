# Terraducktel JetBrains plugin — design

**Date:** 2026-09-13
**Status:** approved design, pending implementation plans
**Branch:** `vscode-extension` (grows PR #30 alongside the VS Code extension)

## Problem

The VS Code extension (`services/vscode/`, spec
`2026-09-12-vscode-extension-design.md`) brings the TDT loops — which
workspace a file belongs to, trigger a plan, read the diff, approve, get told
when a run waits on you — into the editor. Operators on JetBrains IDEs
(PyCharm, IntelliJ IDEA, GoLand, WebStorm, …) get none of it. This plugin is
the same product for the JetBrains platform: feature parity with the VS Code
extension at 0.3.1, the same server surface, the same security posture.

## Decision summary

| Question | Decision |
|---|---|
| Scope | Everything the VS Code extension does at 0.3.1: sidebar trees, trigger/approve, step tailing, plan document, editor ↔ workspace mapping with status bar, approval notifications, profiles with three sign-in modes incl. SSO loopback. Delivered in two plans (see §10). |
| Branch / PR | Same branch as the VS Code extension; PR #30 grows to "IDE clients". One review surface, one contract-guard pattern. |
| IDE range | `sinceBuild = 261` (2026.1), no `untilBuild`. Platform-only dependency (`com.intellij.modules.platform`) so one artifact installs into every JetBrains IDE. |
| Language / toolchain | Kotlin 2.3.x (2026.1 bundles stdlib 2.3.20), JVM target 21 (the 2026.1 floor), Gradle 9.7.1 wrapper, IntelliJ Platform Gradle Plugin 2.18.1, compiled against IntelliJ IDEA 2026.1 (the unified distribution — Community is no longer published since 2025.3). Any JDK ≥ 21 builds it; locally the Toolbox-installed JetBrains Runtime works. |
| Runtime dependencies | None shipped. JSON via the platform's bundled `intellij.libraries.kotlinx.serialization.json` module, concurrency via the bundled kotlinx.coroutines, HTTP via the JDK. Same "no third-party runtime deps" rule as the VS Code extension. |
| Architecture | Native Swing/IntelliJ UI (tool window, actions, dialogs, status-bar widget, notifications) over a typed Kotlin API client, polling. No JCEF-embedded SPA, no new API endpoints. |
| Contract | `services/jetbrains/api_contract.json`, guarded by `services/api/tests/test_jetbrains_api_contract.py` (clone of the VS Code guard), so a router change fails CI naming the plugin feature it broke. |

## 1. Repository layout and toolchain

```
services/jetbrains/
  build.gradle.kts, settings.gradle.kts, gradle.properties, gradlew, gradle/wrapper/
  api_contract.json                       # every endpoint the plugin calls (guarded, §8)
  src/main/resources/META-INF/plugin.xml  # id, name, deps, extensions, actions, settings
  src/main/resources/META-INF/pluginIcon.svg, pluginIcon_dark.svg   # 40×40 brand sigil
  src/main/resources/icons/tdt.svg, tdt_dark.svg                     # 13×13 tool-window mark
  src/main/kotlin/com/terraducktel/jetbrains/
    api/        HttpTransport.kt (HttpURLConnection, timeouts, per-profile insecure TLS)
                TdtClient.kt (bearer, X-Business-Unit, 401→refresh→retry, auth epoch)
                Types.kt (@Serializable DTOs; TERMINAL_RUN_STATUSES / PLAN_LANDED_STATUSES)
                ApiError.kt
    auth/       Credential.kt, SecretStore.kt (PasswordSafe), TokenManager.kt, Jwt.kt, Sso.kt
    settings/   TdtSettings.kt (app-level PersistentStateComponent), Profile.kt,
                TdtConfigurable.kt (Settings → Tools → Terraducktel), ProfileTableModel.kt
    session/    TdtSession.kt (app service: active profile, BU, client, sign-in/out, events)
    state/      Store.kt (app service: polling cache), Grouping.kt (port of grouping.ts), Ids.kt
    toolwindow/ TdtToolWindowFactory.kt, WorkspacesPanel.kt, RunsPanel.kt,
                nodes/*.kt (SimpleNode tree nodes keyed by stable ids)
    actions/    auth/*.kt, workspace/*.kt, run/*.kt, editor/*.kt  (one AnAction per command)
    output/     RunConsole.kt (ConsoleView tab + tailing), PlanDocument.kt (LightVirtualFile)
    editor/     GitProbe.kt, Mapping.kt, EditorStatus.kt (project service), TdtStatusBarWidget.kt
    notifications/ ApprovalWatcher.kt (app service), Notifier.kt
    TdtLog.kt   (Logger + trace gating)
  src/test/kotlin/…                       # JUnit 4: unit tests + light platform tests
  src/test/resources/fixtures/            # grouping fixtures mirrored from the web / VS Code tests
  README.md, CHANGELOG.md
```

- `plugin.xml`: `<id>com.terraducktel.jetbrains</id>`, name **Terraducktel**,
  vendor Terraducktel, `<depends>com.intellij.modules.platform</depends>`,
  `<dependencies><module name="intellij.libraries.kotlinx.serialization.json"/></dependencies>`.
  Nothing depends on the Terraform/HCL or Git4Idea plugins: file matching is
  by extension and git facts come from a `git` shell-out, exactly as in VS Code.
- Gradle: `intellijIdea("2026.1")`, `bundledModule("intellij.libraries.kotlinx.serialization.json")`,
  `testFramework(TestFrameworkType.Platform)`, `junit:junit:4.13.2` (test only).
  `kotlin.compilerOptions.jvmTarget = JVM_21`, `JavaCompile.options.release = 21`.
  `buildPlugin` produces `build/distributions/terraducktel-jetbrains-<version>.zip`.
- Version starts at **0.1.0** (own changelog; parity with VS Code 0.3.1 is a
  feature statement, not a version number).
- Marketplace publishing is out of scope; CI never publishes.

## 2. Connection and sign-in

**Profiles** live in an application-level `PersistentStateComponent`
(`TdtSettings`, stored in `terraducktel.xml`): a list of
`{name, url, uiUrl?, insecureTls}` plus `activeProfile`, and `buByProfile:
Map<name, slug>`. Unlike VS Code — whose Settings UI cannot render an object
list, forcing the `{name: url}` map workaround — a JetBrains `Configurable`
can show a real table, so profiles are structured objects here. Nothing
secret is in that file.

**Secrets** — PasswordSafe, `CredentialAttributes(generateServiceName("Terraducktel", "profile:<name>"))`,
holding the same JSON credential as VS Code: `{kind: "password"|"api_key"|"sso",
refresh_token?, api_key?}`. Access tokens live in memory only. Removing a
profile deletes its credential.

**Modes** (`Terraducktel: Sign in…` action, tool-window toolbar and Tools
menu). A `DialogWrapper` asks `GET /auth/config` first and offers:
1. **SSO** (shown when `oidc_enabled && cli_loopback`; hidden on a remote-dev
   host, where the browser cannot reach the IDE's loopback — the dialog says
   to use an API key, same guidance as the CLI and VS Code).
2. **Email + password** → `POST /auth/token`; store the refresh token.
3. **API key** (`tdt_` prefix validated) → stored as-is; the BU switcher is
   disabled for such profiles because the key pins its own BU.

**SSO** — `com.sun.net.httpserver.HttpServer` on `127.0.0.1:0`, a 32-char
url-safe nonce, `BrowserUtil.browse("<url>/api/v1/auth/oidc/login?cli_port=<port>&cli_nonce=<nonce>")`.
The callback verifies the nonce, stores the refresh token, answers "you can
close this tab", and stops the server. Runs as a cancellable background task
(progress indicator with Cancel); 5-minute timeout; a second sign-in cancels
the first listener.

**Refresh** — identical semantics to the VS Code `TdtClient`: send the access
token; on 401 refresh once (coalesced across concurrent requests), retry
once; a 4xx from `/auth/refresh` deletes the credential and signs the profile
out (once per auth epoch — concurrent 401s do not stack sign-outs); a
transient refresh failure (network, 5xx) fails only the original request.
A request with no credential never reaches the network (`ApiError(401, "Not
signed in")`).

**BU** — `buByProfile[active]`, application-wide (not per project: the cache,
the approval watcher, and every open project's tool window share one BU per
profile, which keeps polling to one loop per IDE). `Switch business unit…`
lists `GET /business-units` (+ `all` for superadmins) in a popup.

**Session events** — `TdtSession` publishes on the application message bus
(`TdtSessionListener`: `profileChanged`, `signedIn`, `signedOut`,
`buChanged`); panels, status bar widgets, and the watcher subscribe.

## 3. Tool window — Workspaces and Runs

One tool window **Terraducktel** (left anchor, brand mark icon) with two
content tabs: **Workspaces** and **Runs**. The Runs tab title carries the
pending-approval count (`Runs · 3`) — the JetBrains equivalent of the VS Code
badge.

**Workspaces tree** — grouped exactly as the web tree and the VS Code
extension: cloud group (AWS account / Azure subscription / GCP project /
"other" = top folder) → region → folders → leaf. `state/Grouping.kt` is a
line-for-line port of `services/vscode/src/state/grouping.ts`, and its test
carries the same fixtures so the three implementations cannot drift silently.
Leaf row: name, drift badge, last-run status icon, tracked branch; tooltip:
id, `tf_working_dir`, repo, environment, tags. Expanding a leaf shows its
recent runs.

**Runs tree** — recent runs for the BU (`GET /runs?limit=<runsLimit>`), fixed
sort as in VS Code (awaiting-approval → active → landed, newest first within a
status). Expanding a run shows its steps (`GET /runs/{id}/steps?include_output=false`,
fetched lazily, cached once terminal).

**Trees** are `Tree` + `StructureTreeModel(SimpleTreeStructure)` + `AsyncTreeModel`;
nodes carry stable ids (`ws:<id>`, `run:<id>`, `step:<run>:<n>`, group keys)
so a refresh preserves expansion and selection. A warning node at the top
shows poll failures / insecure-TLS state, as in VS Code.

**Data** — `Store` (application service) polls `GET /workspaces` and
`GET /runs` every `refreshIntervalSeconds` (default 30) while at least one
project's tool window is visible, and on demand (Refresh action). Single
in-flight poll; a client swap discards stale responses; back-off to 5 minutes
after three consecutive failures. Signed out ⇒ no network. Drift comes from
`drift_status` on each workspace row; `GET /drift/summary` is not called.

**Toolbar** (both tabs): Refresh, Sign in… / Sign out, Switch profile…, Switch
business unit…, Watch run…, Settings. **Context menus** — workspace: Plan,
Apply, Destroy…, Set tracked branch…, Sync from repo, Open in browser, Copy
id. Run: Show plan, Approve…, Reject…, Cancel run, Watch run, Open in
browser, Copy id. Actions' `update()` hides write actions for viewers
(`canWrite()` from the token's role); the server stays the authority.

## 4. Trigger, tail, plan, approve

**Plan / Apply / Destroy** → `POST /workspaces/{id}/runs {command}`; **Set
tracked branch…** → `GET /workspaces/{id}/branches` popup then `PUT
/workspaces/{id} {repo_ref}`. Destroy asks the user to type the workspace
name (same guard as the web UI and VS Code).

**Step tailing** — a run started or watched from the plugin opens a
`ConsoleView` content tab in the Terraducktel tool window ("Run <short id> ·
<workspace>"), polling `GET /runs/{id}/steps?since=<position>` every 2 s
while non-terminal, appending only new output, driven by a cancellable
`Task.Backgroundable`. Terminal states stop the poll and print a one-line
summary. When a run this window follows reaches `awaiting_approval`, a
notification offers **Show plan** and **Approve…** (offered, never
force-opened — VS Code deviation 2), and the run is marked seen so the
background watcher does not announce it again.

**Plan document** — `GET /runs/{id}/plan` opened as a read-only
`LightVirtualFile` named `<workspace>-<short id>.tfplan.txt`. The file type is
the one registered for `*.tf` when a Terraform/HCL plugin is installed,
otherwise plain text. `+` / `-` / `~` lines are coloured through the editor's
`MarkupModel` with the standard diff attributes (`DiffColors.DIFF_INSERTED`
/ `DIFF_DELETED` / `DIFF_MODIFIED`) — theme colours only.

**Approve / Reject / Cancel** — Approve fetches `GET /runs/{id}/graph` and
shows a modal "Apply N to add, N to change, N to destroy, N to replace on
<workspace>?" with **Approve** / **Show plan** / **Cancel** before `POST
/runs/{id}/approve`. Reject asks for an optional reason. Cancel is offered
for `pending | running | planning | awaiting_approval` only. All three refresh
the store.

## 5. Editor ↔ workspace mapping

- `EditorStatus` (project service) listens to `FileEditorManagerListener.selectionChanged`
  for `*.tf`, `*.tfvars`, `*.hcl`. Off the EDT it resolves the file's git root,
  `origin` URL and current branch through `GitProbe` (shell-out to `git`,
  10 s cache per folder, 3 s timeout, realpath-normalised), then
  `Mapping.matchWorkspace` picks the workspace whose normalised `repo_url`
  matches **and** whose `tf_working_dir` is the longest prefix of the file's
  repo-relative directory; `local://` workspaces and unknown remotes fall back
  to a unique `tf_working_dir` prefix; `tf_working_dir="."` never matches.
  `Mapping.kt` ports `services/vscode/src/editor/mapping.ts` with the same
  test cases (incl. `git@host:/abs/path`, `.git` suffix, case).
- **Status bar widget** (`StatusBarWidgetFactory`, `MultipleTextValuesPresentation`):
  `TDT: <workspace> · <last run status>`; click → popup: Plan this leaf, Show
  last plan, Reveal in tool window, Open in browser. No match ⇒ `TDT: not
  imported` with "Open Discover in browser". Hidden while signed out or when
  `statusBarEnabled` is off.
- **Branch awareness**: if the current branch ≠ `repo_ref`, "Plan this leaf"
  offers "Plan on <branch> (pins the workspace)" vs "Plan on <repo_ref>"; the
  first does `PUT /workspaces/{id} {repo_ref}` before the plan.
- Actions also reachable from the editor context menu (`Terraducktel ▸ Plan
  this leaf / Reveal workspace`) and the Tools menu.

## 6. Approval notifications

`ApprovalWatcher` (application service) polls `GET /runs?status=awaiting_approval`
for the active BU every `approvalsPollSeconds` (default 60; positive values
floor at 15; `0` disables), independent of tool-window visibility, stopped
while signed out. Each new run → a sticky balloon in notification group
**Terraducktel approvals** with **Approve…** (goes through the §4 modal),
**Reject…**, **Open**. Behaviour is the VS Code milestone-C contract, kept
verbatim: prime-on-sign-in and on BU/profile change (record the backlog as
seen, notify nothing); silent until primed; `stop()` before `prime()` (loop
epoch) so a stale timer cannot poll mid-prime; 24 h dedupe persisted in
`TdtSettings.notifiedRuns` (pruned on read) so an IDE restart does not
re-notify; tail-toast dedupe via `markSeen`; every failure traced and
swallowed, never an error balloon, never a dead loop.

## 7. Settings UI

**Settings → Tools → Terraducktel** (`TdtConfigurable`, application level):
- Profiles table: Name · API URL · UI URL (optional) · Insecure TLS
  (checkbox), with Add / Remove and an **Active profile** combo fed by the
  table. Removing the active profile clears `activeProfile` and its secret.
- Refresh interval (s, default 30), Runs limit (default 200), Approval poll
  (s, default 60, 0 = off), Show status bar item, Trace requests to the log.
Applying the form republishes `profileChanged` when the active profile's URL
or TLS flag changed, so the session rebuilds its client. Sign-in itself is
not in Settings — it is the action, so secrets never pass through a form
that persists state.

## 8. Contract, security, error handling

- `services/jetbrains/api_contract.json` lists the same 18 endpoints as the
  VS Code contract (auth ×4 incl. the browser-only OIDC login, business
  units, workspaces list/put/branches/sync/runs, runs list/get/steps/graph/
  plan/approve/reject/cancel). `services/api/tests/test_jetbrains_api_contract.py`
  is the VS Code guard pointed at this file.
- Credentials are read only from PasswordSafe; never logged. Trace logging
  (`trace` setting) writes request line + status to `idea.log` under the
  `#com.terraducktel` logger without auth headers or bodies.
- TLS verified by default. `insecureTls` builds a per-connection trust-all
  `SSLSocketFactory` + permissive `HostnameVerifier` on that profile's
  `HttpsURLConnection`s only — never a JVM-wide property (the JDK
  `HttpClient` cannot relax hostname verification per client, which is why
  the transport is `HttpURLConnection`). A warning node in the tree shows
  while an insecure profile is active.
- Timeouts: connect 10 s, read 30 s. Every action surfaces the API's `detail`
  in an error balloon/dialog; long operations run off the EDT.
- No telemetry.

## 9. Testing

- **Unit (JUnit 4, plain JVM):** `TdtClient` against an in-process
  `com.sun.net.httpserver` stub — bearer + BU headers, 401 → refresh → retry
  once, second 401 → single sign-out across concurrent requests, coalesced
  refresh, `detail` propagation, no-credential short-circuit; `TokenManager`
  with an in-memory `SecretStore`; `Sso` nonce verification + timeout;
  `Grouping` with the mirrored fixtures; `Mapping` (`normalizeRepoUrl`,
  `matchWorkspace`); `Store` single-flight and stale-scope discard;
  `ApprovalWatcher` prime/silent/dedupe with a fake clock and client.
- **Platform (`BasePlatformTestCase`, headless):** plugin descriptor loads and
  services resolve; `TdtSettings` round-trips; the tool-window factory builds
  both tabs; signing in with an API key against the stub renders the stub's
  workspaces as tree nodes; a Plan action posts to the stub and the run
  console receives its steps.
- **Backend:** `test_jetbrains_api_contract.py`.
- **Verifier:** `make verify-jetbrains` runs the IntelliJ Plugin Verifier
  against 2026.1 and the newest 2026.2 (downloads IDEs; local only).
- **Manual, once:** install the zip into the local PyCharm 2026.2 against the
  compose stack with `admin@test.com`: password sign-in, tree renders the real
  workspaces, plan a workspace, watch steps, open plan, approve, status bar
  mapping on a `.tf` file in a mapped repo, approval balloon.

## 10. Delivery, documentation, plans

- `make test-jetbrains` (`./gradlew test`), `make build-jetbrains`
  (`./gradlew buildPlugin` → zip), `make verify-jetbrains`. The Makefile
  targets export `JAVA_HOME` from a Toolbox JetBrains Runtime when it is unset
  and no `java` is on `PATH`.
- CI: a `jetbrains` job in `.github/workflows/ci-cd.yml` (Temurin 21, Gradle
  cache) runs `test buildPlugin verifyPluginProjectConfiguration` and uploads
  the zip; `release` / `docs` depend on it like they do on `vscode`.
- Docs: `docs/JETBRAINS.md` (install from zip, profiles, sign-in modes,
  actions, troubleshooting), a `services/jetbrains/` row in `CLAUDE.md`, the
  `docs/ARCHITECTURE.md` clients paragraph, `services/jetbrains/README.md` +
  `CHANGELOG.md`, and PR #30's description.
- **Plan 1 — foundation, tool window, trigger/approve:** scaffold + contract
  guard, transport + client + types, secrets/token manager/profiles/SSO,
  session + sign-in dialog, settings + configurable, store + grouping, tool
  window trees, workspace/run actions, run console, plan document, approve
  modal. Ends installable and usable for the VS Code milestone-A feature set.
- **Plan 2 — editor mapping, notifications, delivery:** git probe + mapping
  + status bar widget + editor actions, approval watcher + balloons + Runs
  badge, icons, Makefile/CI, docs, changelog, verifier run, manual e2e.

## Deviations from the VS Code extension (deliberate)

1. **Structured profiles, not a `{name: url}` map.** The JetBrains settings
   form can render a table; the map shape existed only to satisfy VS Code's
   Settings UI. Semantics (per-profile URL, UI URL, insecure flag, active
   profile picked from a list) are identical.
2. **BU is application-wide per profile, not per project.** VS Code kept a
   per-window override; here one store and one watcher serve every open
   project, so the BU lives with the profile. Switching BU switches it for
   every open project.
3. **Run output is a console tab in the Terraducktel tool window**, not a
   separate output panel — the platform's `ConsoleView` is the idiomatic
   equivalent of an `OutputChannel`.
4. **Transport is `HttpURLConnection`, not the JDK `HttpClient`**, for
   per-connection insecure-TLS (see §8).

## Out of scope (v1)

- Marketplace publishing and signing.
- Depending on the Terraform/HCL or Git plugins (language-aware features,
  VCS-integrated branch pinning).
- Editing workspace settings, creating/importing workspaces — link out to the
  browser, as in VS Code.
- Remote development (Gateway / JetBrains Client) beyond "SSO hidden, use an
  API key".
- Sharing credential files with the `tdt` CLI or the VS Code extension.
