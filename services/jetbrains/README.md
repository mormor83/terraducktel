# Terraducktel for JetBrains IDEs

A JetBrains platform plugin (2026.1+, IntelliJ-based IDEs — developed and
tested against PyCharm) that drives a self-hosted Terraducktel (TDT)
deployment from inside the IDE: a Workspaces/Runs tool window, run
triggering with a live console tail, a diff-coloured plan document, and
gated approvals. Kotlin, no third-party runtime dependencies. See
[`docs/JETBRAINS.md`](../../docs/JETBRAINS.md) in the repository root for the
full user-facing guide.

Marketplace publishing is out of scope for this release — install from disk
only (see below).

## Build

```bash
make build-jetbrains
```

Produces `services/jetbrains/build/distributions/terraducktel-jetbrains-<version>.zip`.
Install it via **Settings → Plugins → ⚙ → Install Plugin from Disk…**.

Equivalent directly with Gradle:

```bash
cd services/jetbrains && JAVA_HOME=<a JDK 17+> ./gradlew buildPlugin
```

## Test

```bash
make test-jetbrains
```

Runs the JUnit test suite (platform test framework — light/heavy fixtures
where a test needs real IDE services, plain JUnit for pure-Kotlin logic like
`state/Grouping.kt`). Equivalent directly:

```bash
cd services/jetbrains && JAVA_HOME=<a JDK 17+> ./gradlew test
```

Both Make targets resolve a JDK automatically if `JAVA_HOME` is unset and no
`java` is on `PATH` — see the Makefile comment above `JB_JAVA_HOME`, or
`docs/JETBRAINS.md`'s "Building and verifying" section.

## Verify

```bash
make verify-jetbrains
```

Runs the IntelliJ Plugin Verifier (`./gradlew verifyPlugin`) against the IDEs
listed in `build.gradle.kts`'s `pluginVerification` block, failing on any
compatibility problem (deprecation warnings are reported, not fatal). Slow
the first time it runs (each listed IDE is downloaded and cached under
`~/.gradle/caches`) — see `docs/JETBRAINS.md`'s "Building and verifying"
section for what it checks and why it's separate from CI's
`verifyPluginProjectConfiguration` step.

## Layout

| Path | What |
|---|---|
| `src/main/kotlin/.../actions/` | `AnAction` implementations: `auth/` (sign in/out, switch profile, switch business unit), `run/` (approve, reject, cancel, show plan, watch run), `workspace/` (plan, apply, destroy, set tracked branch, sync from repo, open in browser, copy id), `editor/` (plan the current file's leaf, reveal its workspace, the combined "actions for current file" popup), plus `RefreshAction` and the shared `ActionUtil` (background-task-to-balloon plumbing). |
| `src/main/kotlin/.../api/` | Typed HTTP client (`TdtClient`, `HttpTransport`) and DTOs (`Types.kt`, `ApiError`) against `/api/v1`. |
| `src/main/kotlin/.../auth/` | Credential/JWT handling, the SSO loopback listener (`Sso.kt`), the PasswordSafe-backed `SecretStore`, and `TokenManager` (refresh-on-401, single-flight). |
| `src/main/kotlin/.../editor/` | Maps the active editor's file to a Terraducktel workspace (`Mapping`, pure Kotlin; `GitProbe` shells out to `git`), publishes that as `EditorStatus` (a project service, started by `EditorStatusStarter`), and renders it as two status-bar widgets (`TdtStatusBarWidget` for the current file, `ProfileStatusBarWidget` for the active profile/BU) plus their shared text/tooltip logic (`StatusText`, pure Kotlin). |
| `src/main/kotlin/.../notifications/` | Polls for runs newly awaiting approval and raises balloons for them: `ApprovalService` (the app service that owns the one `ApprovalWatcher` and wires it to the session/settings/store, started by its own `$Starter`), `ApprovalWatcher` (the poll loop plus the persisted, 24h-deduped "already announced" set), `ApprovalNotifier` (turns a notice into a sticky balloon with its Approve…/Reject…/Open actions), and `Rearm` (the sign-out-races-prime guard used when re-arming the watcher on sign-in/profile/BU switch). |
| `src/main/kotlin/.../output/` | Run console tailing (`RunConsoles`, `RunTail`), the diff-coloured plan document (`PlanDocument`), the gated approve modal (`Approvals`), and the shared trigger/watch/announce entry points (`RunActions`). |
| `src/main/kotlin/.../session/` | `TdtSession` (the application-level light service holding sign-in state and the active client), `SignInFlow` (interactive sign-in UI), and the startup activity/listener that wire it in. |
| `src/main/kotlin/.../settings/` | Persistent settings (`TdtSettings`), the Settings → Tools → Terraducktel page (`TdtConfigurable`), and the `Profile` model. |
| `src/main/kotlin/.../state/` | `Store` (the polling store the trees read from) and `Grouping` (pure Kotlin port of the web UI's workspace-tree grouping — no platform imports, so it's covered by plain JUnit). |
| `src/main/kotlin/.../toolwindow/` | The tool window factory and its two tree panels (Workspaces, Runs), shared tree plumbing (`TreePanel`), and the node types under `nodes/`. |
| `src/main/resources/META-INF/plugin.xml` | Plugin descriptor: extensions, actions, and menu wiring. |
| `src/test/kotlin/` | Mirrors the `main` package layout; `testutil/` holds a stub HTTP server and an in-memory secret store shared across tests. |
| `api_contract.json` | Every API endpoint the plugin calls, with the plugin feature that depends on it. |
| `build.gradle.kts` | Kotlin/IntelliJ Platform Gradle plugin configuration (`sinceBuild = "261"`, no `untilBuild`). |

## The API contract guard

`api_contract.json` lists every `/api/v1` endpoint the plugin depends on.
`services/api/tests/test_jetbrains_api_contract.py` asserts each one still
exists in the running API's OpenAPI schema — if a router renames or removes
a path this plugin calls, that test fails and names the plugin feature that
just broke. When you add a call to a new endpoint, add its entry to
`api_contract.json` in the same change (method, path relative to `/api/v1`,
and a short `used_by` description) so the guard covers it from the start.
