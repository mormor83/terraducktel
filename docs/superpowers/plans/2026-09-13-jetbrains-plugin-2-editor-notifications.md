# JetBrains plugin — Plan 2: editor mapping, approval notifications, delivery

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish feature parity with the VS Code extension 0.3.1: the active Terraform file is mapped to its TDT workspace (status-bar widget with Plan / Show last plan / Reveal / Open, branch-aware planning), runs awaiting approval raise balloons with Approve / Reject / Open, the Runs tab badge stays live, and the plugin ships with brand icons, CI, make targets, docs and a changelog.

**Architecture:** Two pure Kotlin ports from the VS Code sources (`editor/{git,mapping}.ts` → `editor/GitProbe.kt` + `Mapping.kt`; `notifications/{approvals,rearm}.ts` → `notifications/ApprovalWatcher.kt` + `Rearm.kt`) with their test cases carried over verbatim, wrapped in thin IntelliJ adapters: a project-level `EditorStatus` service feeding a `StatusBarWidget`, and an application-level watcher feeding `Notification`s. Everything network-bound runs off the EDT.

**Tech Stack:** as Plan 1 (Kotlin 2.3.21, IntelliJ Platform Gradle Plugin 2.18.1, `intellijIdea("2026.1")`, JUnit 4, `StubServer`), plus GitHub Actions (`actions/setup-java` Temurin 21, `gradle/actions/setup-gradle`).

**Spec:** `docs/superpowers/specs/2026-09-13-jetbrains-plugin-design.md` (§5, §6, §9, §10)

## Global Constraints

- Everything in Plan 1's Global Constraints still binds (plugin id, `sinceBuild=261`, platform-only dependency, no third-party runtime deps, boot-classpath kotlinx.serialization without a `<module>` declaration, JVM 21, no network on the EDT, `ActionUpdateThread.BGT`, secrets only in PasswordSafe) — **except the commit trailer**, which is now `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` followed by the same `Claude-Session:` line.
- Plan 1 shipped `approvalsPollSeconds` and `statusBarEnabled` as settings rows labelled "(reserved for a future release)" because nothing read them. This plan wires both: **remove that label from each row as its feature lands** (Task 2 for `statusBarEnabled`, Task 4 for `approvalsPollSeconds`) and update the matching sentences in `docs/JETBRAINS.md`. Likewise `plugin.xml`'s `<description>` may mention approval notifications again once Task 4 lands, and the two `api_contract.json` `used_by` strings for `GET /runs` and `GET /runs/{run_id}/graph` should regain their approval-notification mention.
- Plan 1 left two forward hooks marked "Unused in 0.1.0": `TreePanel.revealWorkspace` (Task 2 calls it) and `RunActions.onAwaitingHook` (Task 4 assigns it). Remove those comments as each is wired.
- `RunActions.trigger(project, ws, command, branch)` now goes through the `confirmApply`/`confirmDestroy` seams added in plan 1's final wave; Task 2's "Plan this leaf" must call `trigger` (not a private copy) so those guards still apply.
- Git facts come from a `git` shell-out (`git rev-parse --show-toplevel`, `git remote get-url origin`, `git rev-parse --abbrev-ref HEAD`) with a 10 s per-folder cache and a 3 s timeout — never from the Git4Idea plugin.
- Mapping rules (spec §5, VS Code `mapping.ts`): normalised `repo_url` must match the folder's `origin` **and** `tf_working_dir` must be the longest prefix of the file's repo-relative directory; `local://` workspaces and unknown remotes match by unique `tf_working_dir` prefix alone; `tf_working_dir` empty or `"."` never matches; repo paths compare case-folded; `ssh://host:22/` equals `git@host:`.
- Approval watcher contract (spec §6): poll `GET /runs?status=awaiting_approval&limit=100` every `approvalsPollSeconds` (default 60; positive values floor at 15; 0 disables); prime-on-sign-in and on profile/BU change; silent until primed; `stop()` before `prime()`; 24 h dedupe persisted in `TdtSettings.state.notifiedRuns`; `markSeen` from the run-console landing toast; every failure traced and swallowed.
- Balloons carry **Approve…** (goes through `Approvals.approve`), **Reject…**, **Open**. Nothing is applied from a click alone.
- CI never publishes to the Marketplace.

---

## File map

| File | Responsibility |
|---|---|
| `editor/GitProbe.kt`, `editor/Mapping.kt` | Pure ports + tests (Task 1) |
| `editor/EditorStatus.kt`, `editor/StatusText.kt`, `editor/TdtStatusBarWidget.kt`, `editor/ProfileStatusBarWidget.kt`, `actions/editor/{PlanCurrentFileAction,RevealCurrentWorkspaceAction,CurrentFileActionsAction}.kt` | Editor ↔ workspace mapping UI (Task 2) |
| `notifications/ApprovalWatcher.kt`, `notifications/Rearm.kt` | Pure ports + tests (Task 3) |
| `notifications/ApprovalNotifier.kt`, `notifications/ApprovalService.kt` | Balloons + wiring (Task 4) |
| `META-INF/pluginIcon.svg`, `pluginIcon_dark.svg`, `.github/workflows/ci-cd.yml`, `Makefile`, `docs/JETBRAINS.md`, `services/jetbrains/{README,CHANGELOG}.md`, `docs/ARCHITECTURE.md`, `CLAUDE.md` | Delivery (Task 5) |

Kotlin paths are relative to `services/jetbrains/src/main/kotlin/com/terraducktel/jetbrains/`; tests to `services/jetbrains/src/test/kotlin/com/terraducktel/jetbrains/`. Build/test with `JAVA_HOME=$HOME/.local/share/JetBrains/Toolbox/apps/pycharm/jbr ./gradlew --console=plain test`.

---

### Task 1: GitProbe and Mapping (ports of git.ts and mapping.ts)

**Files:**
- Create: `editor/GitProbe.kt`, `editor/Mapping.kt`
- Test: `src/test/.../editor/GitProbeTest.kt`, `src/test/.../editor/MappingTest.kt`

**Interfaces:**
```kotlin
data class GitInfo(val root: String, val remoteUrl: String? = null, val branch: String? = null)
typealias ExecFn = (cmd: String, args: List<String>, cwd: String, timeoutMs: Long) -> String   // throws on non-zero exit / timeout
class GitProbe(ttlMs: Long = 10_000, timeoutMs: Long = 3_000, exec: ExecFn = GitProbe.defaultExec) {
    fun info(filePath: String): GitInfo?      // null when not in a git checkout; never throws; blocking — call off the EDT
    fun invalidate(root: String? = null)
    companion object { val defaultExec: ExecFn /* ProcessBuilder, redirectErrorStream=false, waitFor(timeout) → destroyForcibly on timeout */ }
}
data class Match(val ws: Workspace, val exact: Boolean)
object Mapping {
    fun normalizeRepoUrl(url: String?): String?
    fun relativeDir(gitRoot: String, filePath: String): String?      // posix-separated; "" at root; null when outside
    fun matchWorkspace(workspaces: List<Workspace>, relativeDir: String, remoteUrl: String?): Match?
}
```
- [ ] **Step 1: Failing tests** — port **every** case of `services/vscode/test/unit/mapping.test.ts` (12 `it` blocks incl. the `it.each` table: `https://github.com/Acme/Infra.git`→`github.com/acme/infra`, scp form, `ssh://`, `http://forgejo:3002/infra/live/`, credentials in URL, `local:///mnt/local-repos/probe`→`local:/mnt/local-repos/probe`, `git@forgejo.internal:/repos/infra.git`→`forgejo.internal/repos/infra`; empty/null/garbage → null; Windows drive guards `C:\Users\x\repo` and `c:/x/repo` → null; `ssh://git@host:22/o/r` == `git@host:o/r`; `relativeDir` root/child/outside/`..foo` sibling; `matchWorkspace` longest prefix, repo mismatch excluded, `local://` by path, unknown remote unique vs ambiguous, `tf_working_dir="."` never matches, `exact` flag) and of `test/unit/git.test.ts` (fake `exec` recording calls: root cached per dir within TTL, remote+branch cached per root, `HEAD` detached → branch null, failing `exec` → null info, `invalidate(root)` drops both caches). Read both TS test files; same inputs, same expectations. Use `java.nio.file.Paths` for `relativeDir` (`Paths.get(gitRoot).relativize(Paths.get(filePath).parent)`; a result starting with `..` or an `IllegalArgumentException` (different roots) → null; join with `/`).
- [ ] **Step 2: Implement** as line-by-line ports. `normalizeRepoUrl` regexes come from `mapping.ts` verbatim (Kotlin `Regex`); host/path lower-cased; `.git` suffix and trailing slashes stripped; ssh port 22 dropped. `GitProbe.defaultExec`: `ProcessBuilder(listOf(cmd) + args).directory(File(cwd)).redirectErrorStream(false)`, read stdout fully on a thread, `waitFor(timeoutMs, MILLISECONDS)` else `destroyForcibly()` + throw; non-zero exit → throw.
- [ ] **Step 3: `./gradlew test --tests '*editor*'` → green; commit** `feat(jetbrains): git probe and file → workspace mapping`.

---

### Task 2: EditorStatus, status-bar widgets, editor actions

**Files:**
- Create: `editor/StatusText.kt`, `editor/EditorStatus.kt`, `editor/TdtStatusBarWidget.kt`, `editor/ProfileStatusBarWidget.kt`, `actions/editor/PlanCurrentFileAction.kt`, `RevealCurrentWorkspaceAction.kt`, `CurrentFileActionsAction.kt`
- Modify: `plugin.xml` (`<statusBarWidgetFactory>` ×2, `<projectService>` not needed for `@Service(PROJECT)`, editor popup group `Terraducktel.EditorMenu` under `EditorPopupMenu`, Tools submenu entries), `toolwindow/TdtToolWindowFactory.kt` (add `fun revealWorkspace(project, wsId)` that activates the tool window, selects the Workspaces tab and calls `TreePanel.revealWorkspace`)
- Test: `src/test/.../editor/StatusTextTest.kt`, `src/test/.../editor/EditorStatusTest.kt` (BasePlatformTestCase)

**Interfaces:**
```kotlin
data class CurrentFile(val ws: Workspace, val git: GitInfo?, val exact: Boolean, val resolvedPath: String)
/** Pure presentation of the status item — port of EditorStatus.set() */
object StatusText {
    data class View(val text: String, val tooltip: String, val severity: Severity /* NONE, WARNING (awaiting_approval), ERROR (failed) */)
    fun mapped(cur: CurrentFile, lastRun: Run?): View          // "TDT: <ws> · <status>", tooltip "<tf_working_dir>[ (parent leaf)][ · on <branch> (tracks <repo_ref>)]\nClick for actions"
    fun unmapped(git: GitInfo?): View                          // "TDT: not imported" + the two tooltips from status.ts
}
@Service(Service.Level.PROJECT) class EditorStatus(val project: Project, val scope: CoroutineScope) : Disposable {
    @Volatile var current: CurrentFile?; @Volatile var view: StatusText.View?   // null → widget hidden
    fun refresh()                        // seq-guarded, off-EDT: realpath → GitProbe → Mapping; then EDT: widget.update
    fun planCurrent()                    // re-probes git (invalidate root) → branch choice popup when branch ≠ repo_ref → RunActions.trigger(project, ws, "plan", branch)
    fun actionsPopup(): ListPopup        // Plan this leaf / Show last plan (when a last run exists) / Reveal in tool window / Open in browser; unmapped → opens <uiUrl>/ in the browser
    fun addListener(parent: Disposable, l: () -> Unit)
    companion object { fun getInstance(project: Project): EditorStatus = project.service() }
}
```
- [ ] **Step 1: Failing tests** — `StatusTextTest`: mapped with last run `failed` → ERROR severity and text `TDT: vpc · failed`; awaiting_approval → WARNING; no last run → `TDT: vpc`; parent leaf + branch drift → tooltip contains `(parent leaf)` and `on feature/x (tracks main)`; unmapped with git → "No Terraducktel workspace covers this path…", without → "Not inside a git checkout…". `EditorStatusTest` (platform): create a temp git repo (`git init`, `git remote add origin https://github.com/acme/infra.git`, commit a file at `envs/prod/main.tf`), point `Store.workspaces` at `[ws(tf_working_dir="envs/prod", repo_url="https://github.com/acme/infra")]`, sign the session in with an API key against a `StubServer`, open the file with `myFixture.openFileInEditor(VfsUtil.findFileByIoFile(...))`, call `EditorStatus.getInstance(project).refresh()` and wait (poll ≤ 5 s) until `current?.ws?.name == "prod"`; a `.py` file → `view == null`; signed out → `view == null`. Skip the platform test with an `Assume.assumeTrue(gitAvailable)` guard when `git` is not on PATH.
- [ ] **Step 2: Implement** — `EditorStatus` subscribes (via `project.messageBus.connect(this)`) to `FileEditorManagerListener.FILE_EDITOR_MANAGER` (`selectionChanged` → `refresh()`), `Store.addListener`, `TdtSessionListener.TOPIC`, `TdtSettingsListener.TOPIC`; `refresh()` = `scope.launch(Dispatchers.IO)` with an `AtomicInteger seq` guard (port of `status.ts`): enabled (`statusBarEnabled`) && active file is `*.tf|*.tfvars|*.hcl` on the local file system && signed in, else `view = null`; `resolvedPath = File(path).toPath().toRealPath()` (fallback to the raw path); `GitProbe.info` → `Mapping.relativeDir` → `Mapping.matchWorkspace(store.workspaces, rel, git.remoteUrl)`; publish `view` and fire listeners; the widget calls `StatusBar.updateWidget(id)`. `TdtStatusBarWidget` (`StatusBarWidgetFactory` id `Terraducktel.CurrentFile`, `isAvailable(project)` = `statusBarEnabled`) implements `StatusBarWidget.MultipleTextValuesPresentation`: `getSelectedValue()` = view.text, `getTooltipText()` = view.tooltip, `getPopup()` = `EditorStatus.actionsPopup()`; the widget is hidden when `view == null` (return `null` from `getSelectedValue()` and let `isAvailable` + `updateWidget` handle visibility — verify in the IDE that an empty value hides the item; if not, remove/add via `StatusBar.removeWidget`). `ProfileStatusBarWidget` (id `Terraducktel.Profile`): text `<profile>[ · <bu>]`, tooltip "Terraducktel profile — click to switch", popup = the profile chooser from `SwitchProfileAction`; hidden when no profiles exist. `planCurrent()` ports `status.ts planCurrent` (fresh probe after `invalidate(root)`; popup "Plan on <branch> (pins the workspace)" / "Plan on <repo_ref>"; then `RunActions.trigger(project, ws, "plan", branch)`). Actions: `PlanCurrentFileAction` (enabled iff `current != null && canWrite()`), `RevealCurrentWorkspaceAction`, `CurrentFileActionsAction` (shows `actionsPopup()` under the caret). `plugin.xml`: `<statusBarWidgetFactory id="Terraducktel.CurrentFile" implementation="…TdtStatusBarWidgetFactory" order="after Position"/>`, same for `Terraducktel.Profile` (`order="before Terraducktel.CurrentFile"`); group `Terraducktel.EditorMenu` (text "Terraducktel", popup) added to `EditorPopupMenu` with Plan This Leaf / Reveal Workspace; add both to the Tools submenu.
- [ ] **Step 3: `./gradlew test` → green; commit** `feat(jetbrains): current-file workspace mapping with status-bar widgets and editor actions`.

---

### Task 3: ApprovalWatcher and Rearm (ports of approvals.ts and rearm.ts)

**Files:**
- Create: `notifications/ApprovalWatcher.kt`, `notifications/Rearm.kt`
- Test: `src/test/.../notifications/ApprovalWatcherTest.kt`, `src/test/.../notifications/RearmTest.kt`

**Interfaces:**
```kotlin
interface SeenStore { fun get(): Map<String, Long>; fun set(v: Map<String, Long>) }
data class ApprovalNotice(val run: Run, val workspaceName: String, val summary: GraphSummary?)
class ApprovalWatcher(
    private val client: () -> TdtClient?, private val workspaceName: (String) -> String, private val notify: (ApprovalNotice) -> Unit,
    private val seen: SeenStore, private val now: () -> Long = System::currentTimeMillis, private val trace: ((String) -> Unit)? = null,
    private val ttlMs: Long = 24 * 3_600_000, private val scope: CoroutineScope,
) : Disposable {
    fun prime()               // blocking: record the backlog as seen, notify nobody; sets primed only after seen.set() succeeded
    fun markSeen(runId: String)
    fun poll()                // blocking, single-flight
    fun start(intervalMs: Long); fun stop()   // coroutine loop; stop() cancels; a stop() during an in-flight poll must not be undone by the loop
    override fun dispose()
}
class Rearm(private val key: () -> String?, private val prime: () -> Unit, private val start: () -> Unit, private val stop: () -> Unit) {
    fun invoke()              // blocking; generation-guarded port of createRearm(): stop() before prime() on a key change; a superseded call never start()s
}
```
- [ ] **Step 1: Failing tests** — port **every** case of `services/vscode/test/unit/approvals.test.ts` and `test/unit/rearm.test.ts` (read them; fake client = a `TdtClient` over `StubServer` whose `/runs` handler serves a mutable list; fake clock via `now`; in-memory `SeenStore`, including a variant whose `set` throws): prime records all and notifies none; first poll after prime notifies only new runs, once; 24 h TTL prune; failed prime → next poll silent and records; `seen.set` throwing during prime keeps `primed=false`; `markSeen` suppresses the later poll's notice; `getGraph` failure → notice with `summary == null`; one `notify` throwing does not stop the rest of the batch; `stop()` mid-poll prevents re-arm; `Rearm`: primes once per key, `stop` called before `prime` on key change, superseded generation never starts, key → null stops.
- [ ] **Step 2: Implement** as line-by-line ports (the `loopEpoch` becomes cancelling the loop `Job`; `poll()` single flight via an `AtomicBoolean`/`ReentrantLock.tryLock` with waiters joining — match `inflight` semantics: a second `poll()` during a poll returns after the first completes without polling again).
- [ ] **Step 3: `./gradlew test --tests '*notifications*'` → green; commit** `feat(jetbrains): approval watcher with prime-on-sign-in and 24 h dedupe`.

---

### Task 4: Balloons, wiring, badge

**Files:**
- Create: `notifications/ApprovalNotifier.kt`, `notifications/ApprovalService.kt`
- Modify: `session/TdtSession.kt` (publish `sessionChanged` already exists — subscribe from `ApprovalService`), `output/RunConsoles.kt`/`RunActions` (`onAwaitingHook = { ApprovalService.getInstance().markSeen(it.id) }`), `plugin.xml` (`<notificationGroup id="Terraducktel approvals" displayType="STICKY_BALLOON"/>`, `<applicationListeners>`/`<postStartupActivity>` to instantiate `ApprovalService` at startup: use `<backgroundPostStartupActivity implementation="…ApprovalService$Starter"/>` calling `ApprovalService.getInstance()`), `toolwindow/RunsPanel.kt` (badge title already updates on store change — confirm it also updates when the watcher's poll finds new runs by calling `Store.refresh()` after a batch with notices)
- Test: `src/test/.../notifications/ApprovalServiceTest.kt` (BasePlatformTestCase)

**Interfaces:**
```kotlin
object ApprovalNotifier { fun show(project: Project?, n: ApprovalNotice) }  // group "Terraducktel approvals": title "TDT: <ws> <command> awaits approval", content "+a ~c -d ±r" when summary != null; actions Approve… → Approvals.approve(project, run), Reject… → RejectAction logic, Open → BrowserUtil.browse("<uiUrl>/runs/<id>")
@Service(Service.Level.APP) class ApprovalService(val scope: CoroutineScope) : Disposable {
    fun markSeen(runId: String); fun rearm()     // Rearm over key = "<profile>:<bu>" when signed in else null; interval = approvalsPollSeconds (0 → off; else max(15, v) * 1000)
    class Starter : ProjectActivity { override suspend fun execute(project: Project) { getInstance() } }
    companion object { fun getInstance(): ApprovalService = service() }
}
```
`SeenStore` implementation reads/writes `TdtSettings.state.notifiedRuns` (persisted by the settings component). Project for dialogs/balloons: `ProjectUtil.getActiveProject() ?: ProjectManager.getInstance().openProjects.firstOrNull()`.
- [ ] **Step 1: Failing platform test** — with a `StubServer` serving `/runs?status=awaiting_approval` (one run after prime), sign in, `ApprovalService.getInstance().rearm()` then trigger `poll()` (expose `internal fun pollNow()`), assert a notification in group `Terraducktel approvals` was posted (subscribe to `Notifications.TOPIC` on the test disposable) with three actions; a second poll posts nothing; after `signOut()` the watcher is stopped (no `/runs` requests for 2 × interval with interval floored to a test-injectable 100 ms — expose `internal var minIntervalMs`).
- [ ] **Step 2: Implement** — `ApprovalService` builds the watcher with `client = { TdtSession.getInstance().clientOrNull() }`, `workspaceName = { Store.getInstance().workspace(it)?.name ?: it.take(8) }`, `notify = { ApprovalNotifier.show(activeProject(), it); Store.getInstance().refresh() }`, `seen = SettingsSeenStore`, `trace = TdtLog::trace when settings.trace`; subscribes to `TdtSessionListener.TOPIC` and `TdtSettingsListener.TOPIC` → `scope.launch(Dispatchers.IO) { rearm() }`; calls `rearm()` once at construction. Sticky balloons (`NotificationType.INFORMATION`, `setImportant(true)`), actions via `NotificationAction.createSimpleExpiring`.
- [ ] **Step 3: `./gradlew test` → green; commit** `feat(jetbrains): approval balloons wired to the session and Runs badge`.

---

### Task 5: Icons, CI, make verify, docs, changelog

**Files:**
- Create: `services/jetbrains/src/main/resources/META-INF/pluginIcon.svg`, `pluginIcon_dark.svg`
- Modify: `.github/workflows/ci-cd.yml` (new `jetbrains` job; add it to both `needs:` lists that name `vscode`), `Makefile` (`verify-jetbrains`), `docs/JETBRAINS.md` (complete the mapping/notifications/verify sections), `services/jetbrains/README.md`, `services/jetbrains/CHANGELOG.md` (0.1.0 final feature list), `docs/ARCHITECTURE.md` + `CLAUDE.md` (mention approvals/mapping if the Plan 1 wording omitted them), `build.gradle.kts` (`pluginVerification { ides { recommended() } }` or explicit `ide(IntelliJPlatformType.IntellijIdea, "2026.1")` + the newest 2026.2)

- [ ] **Step 1: Icons** — 40×40 `pluginIcon.svg` derived from `docs/branding/td/brand/terraducktel-mark.svg` (the brand duck mark; keep its colours), `pluginIcon_dark.svg` the same with a light silhouette if the mark is dark-on-transparent (inspect the SVG first). The tool-window icon from Plan 1 stays.
- [ ] **Step 2: CI job**
```yaml
  jetbrains:
    name: JetBrains plugin (unit + platform tests, build zip)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-java@v4
        with: { distribution: temurin, java-version: "21" }
      - uses: gradle/actions/setup-gradle@v4
      - name: Test + build
        working-directory: services/jetbrains
        run: ./gradlew --console=plain --no-daemon test buildPlugin verifyPluginProjectConfiguration
      - uses: actions/upload-artifact@v4
        with:
          name: terraducktel-jetbrains
          path: services/jetbrains/build/distributions/*.zip
```
Add `jetbrains` to the two `needs:` lists (`release`, `docs`) next to `vscode`. Confirm `gradlew` is executable in git (`git ls-files -s services/jetbrains/gradlew` shows mode 100755).
- [ ] **Step 3: Verifier** — `make verify-jetbrains` → `./gradlew verifyPlugin` with `intellijPlatform { pluginVerification { ides { ide(IntelliJPlatformType.IntellijIdea, "2026.1"); recommended() } } }` in `build.gradle.kts`; run it once locally and paste the summary (compatibility problems must be zero; deprecations are allowed but listed in the report) into the task report.
- [ ] **Step 4: Docs** — `docs/JETBRAINS.md`: add "Current file → workspace" (status bar item, branch pinning choice, unknown-remote fallback, `tf_working_dir="."` never matches), "Approval notifications" (prime-on-sign-in, 24 h dedupe, poll interval floor), "Building and verifying" (`make test-jetbrains`, `build-jetbrains`, `verify-jetbrains`, JAVA_HOME fallback), "Differences from the VS Code extension" (the four deviations from the spec). `CHANGELOG.md` 0.1.0 lists all Plan 1 + Plan 2 features. `README.md` layout table complete.
- [ ] **Step 5: Manual e2e** against the local compose stack in PyCharm 2026.2 (install the fresh zip): open a `.tf` file from a repo whose workspace is imported (the local-repo probe workspaces from the Proxmox e2e, or import `terraform/` from the compose Forgejo), confirm the status bar item and its popup, "Plan this leaf" from a non-tracked branch offers the pin choice, trigger a plan from the web UI as another user and confirm the balloon arrives within the poll interval with three actions, Approve… shows the graph modal. Record outcomes (including anything that could not be exercised) in the task report.
- [ ] **Step 6: Commit** `feat(jetbrains): brand icons, CI job, plugin verifier target, docs and changelog`.
