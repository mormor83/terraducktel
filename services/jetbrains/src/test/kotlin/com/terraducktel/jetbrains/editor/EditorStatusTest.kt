package com.terraducktel.jetbrains.editor

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.vfs.LocalFileSystem
import com.intellij.testFramework.PlatformTestUtil
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.terraducktel.jetbrains.api.TdtClient
import com.terraducktel.jetbrains.api.Workspace
import com.terraducktel.jetbrains.auth.PasswordSafeSecretStore
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.settings.Profile
import com.terraducktel.jetbrains.settings.TdtSettings
import com.terraducktel.jetbrains.state.Store
import com.terraducktel.jetbrains.testutil.InMemorySecretStore
import com.terraducktel.jetbrains.testutil.StubServer
import java.io.File
import java.nio.file.Files
import java.util.concurrent.TimeUnit

/**
 * Platform test for [EditorStatus]: drives a real active editor over a real (temp) git checkout
 * and a real [Mapping]/[GitProbe] resolution, the same wiring `refresh()` uses in production.
 * Signs the (app-level) [TdtSession] in the same way [com.terraducktel.jetbrains.session
 * .TdtSessionTest] does — an in-memory secret store + a neutered [Store] client provider, so the
 * store never makes a real network call — then seeds [Store] directly via `setSnapshotForTest`.
 *
 * Skipped outright when `git` isn't on `PATH` via [shouldRunTest] — NOT `Assume.assumeTrue` inside
 * [setUp]: [BasePlatformTestCase] is a JUnit 3 `TestCase` and the Gradle build's `test` task uses
 * `useJUnit()`, so an `AssumptionViolatedException` thrown from `setUp()` is reported as a FAILURE,
 * not a skip, and — because `setUp()` would have aborted before [originalClientProvider] was ever
 * assigned — [tearDown] then throws `UninitializedPropertyAccessException`, burying the real cause
 * under a confusing second one. [shouldRunTest] is the platform's own mechanism for this exact
 * case: `UsefulTestCase.runBare()` checks it BEFORE calling `setUp()`/`tearDown()` at all — a false
 * return skips the whole test (both `setUp()` and `tearDown()`) cleanly, so [originalClientProvider]
 * never needs to survive a partially-run `setUp()`.
 *
 * [originalClientProvider] is a plain `var` (not `lateinit`) defaulted to a harmless placeholder at
 * declaration rather than left to throw if ever read before [setUp] assigns the real value — note
 * it can NOT be initialised from `Store.getInstance()` at declaration, tempting as that looks:
 * JUnit3's `TestSuite` constructs one instance of this class per test method up front, to build the
 * suite, before the Gradle IntelliJ-platform test runner has bootstrapped `ApplicationManager`'s
 * Application — so any field initialiser touching a platform service throws `NullPointerException`
 * there and kills every test in the class before `shouldRunTest()` is ever consulted.
 */
class EditorStatusTest : BasePlatformTestCase() {

    private val session get() = TdtSession.getInstance()
    private lateinit var secretStore: InMemorySecretStore
    private var originalClientProvider: () -> TdtClient? = { null }
    private var repoRoot: File? = null

    override fun shouldRunTest(): Boolean = super.shouldRunTest() && gitAvailable()

    override fun setUp() {
        super.setUp()
        secretStore = InMemorySecretStore()
        session.secretStoreFactory = { secretStore }
        originalClientProvider = Store.getInstance().clientProvider
        Store.getInstance().clientProvider = { null }
    }

    override fun tearDown() {
        try {
            offEdt { session.signOut() }
            TdtSettings.getInstance().loadState(TdtSettings.State())
            session.secretStoreFactory = { PasswordSafeSecretStore() }
            Store.getInstance().clientProvider = originalClientProvider
            offEdt { session.reload() }
            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
            repoRoot?.deleteRecursively()
        } finally {
            super.tearDown()
        }
    }

    private fun gitAvailable(): Boolean = runCatching {
        val p = ProcessBuilder("git", "--version").start()
        p.waitFor(5, TimeUnit.SECONDS) && p.exitValue() == 0
    }.getOrDefault(false)

    private fun <T> offEdt(block: () -> T): T =
        ApplicationManager.getApplication().executeOnPooledThread<T> { block() }.get(10, TimeUnit.SECONDS)

    private fun git(vararg args: String, cwd: File) {
        val p = ProcessBuilder(listOf("git") + args).directory(cwd).redirectErrorStream(true).start()
        val out = p.inputStream.bufferedReader().readText()
        check(p.waitFor(10, TimeUnit.SECONDS)) { "git ${args.joinToString(" ")} timed out" }
        check(p.exitValue() == 0) { "git ${args.joinToString(" ")} failed: $out" }
    }

    /** A real checkout at `<root>/envs/prod/main.tf`, remote `origin` = `acme/infra`. */
    private fun initRepo(): File {
        val root = Files.createTempDirectory("tdt-editorstatus-").toFile()
        repoRoot = root
        git("init", cwd = root)
        git("config", "user.email", "test@example.com", cwd = root)
        git("config", "user.name", "Test", cwd = root)
        git("remote", "add", "origin", "https://github.com/acme/infra.git", cwd = root)
        File(root, "envs/prod").mkdirs()
        File(root, "envs/prod/main.tf").writeText("# tf\n")
        git("add", "-A", cwd = root)
        git("commit", "-m", "initial", cwd = root)
        return root
    }

    /** Creates and switches to [branch] at the current commit — used by the `planCurrent()` tests
     *  to get a checked-out branch that deterministically differs from the workspace's
     *  `repo_ref`, regardless of the ambient `init.defaultBranch` this machine's `git` uses. */
    private fun checkoutBranch(root: File, branch: String) = git("checkout", "-b", branch, cwd = root)

    private fun signIn(srv: StubServer) {
        val profile = Profile(name = "p", url = srv.url)
        TdtSettings.getInstance().state.profiles = mutableListOf(profile)
        TdtSettings.getInstance().state.activeProfile = "p"
        offEdt { session.reload() }
        offEdt { session.signInWithApiKey("tdt_x") }
        // signInWithApiKey() itself fires an async Store.refresh(); the client provider is
        // neutered above, so this is a harmless, idempotent clear() — draining it here means the
        // setSnapshotForTest() call right after can never be clobbered by it landing late.
        offEdt { Store.getInstance().refreshAndWait() }
    }

    private fun seedWorkspace() {
        Store.getInstance().setSnapshotForTest(
            listOf(
                Workspace(
                    id = "w1",
                    name = "prod",
                    tf_working_dir = "envs/prod",
                    repo_url = "https://github.com/acme/infra",
                ),
            ),
            emptyList(),
        )
    }

    private fun openInEditor(file: File) {
        val vf = LocalFileSystem.getInstance().refreshAndFindFileByIoFile(file)
        assertNotNull("could not find $file in the VFS", vf)
        myFixture.openFileInEditor(vf!!)
    }

    private fun waitUntil(timeoutMs: Long = 5_000, cond: () -> Boolean) {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (!cond() && System.currentTimeMillis() < deadline) {
            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
            Thread.sleep(50)
        }
    }

    fun testActiveTerraformFileResolvesToItsWorkspace() {
        StubServer().use { srv ->
            val root = initRepo()
            signIn(srv)
            seedWorkspace()
            openInEditor(File(root, "envs/prod/main.tf"))

            val status = EditorStatus.getInstance(project)
            offEdt { status.refresh() }
            waitUntil { status.current?.ws?.name == "prod" }

            assertEquals("prod", status.current?.ws?.name)
            assertNotNull(status.view)
            assertEquals("TDT: prod", status.view?.text)
        }
    }

    fun testSwitchingToANonTerraformFileUnmapsTheWidget() {
        StubServer().use { srv ->
            val root = initRepo()
            signIn(srv)
            seedWorkspace()
            openInEditor(File(root, "envs/prod/main.tf"))

            val status = EditorStatus.getInstance(project)
            offEdt { status.refresh() }
            waitUntil { status.current?.ws?.name == "prod" }
            // Prove refresh() actually ran and mapped it — otherwise the assertions below would
            // pass just as well if refresh() (or the .py switch) never did anything at all.
            assertEquals("prod", status.current?.ws?.name)
            assertNotNull(status.view)

            val py = File(root, "envs/prod/script.py").apply { writeText("print(1)\n") }
            openInEditor(py)
            offEdt { status.refresh() }
            waitUntil { status.current == null }

            assertNull(status.current)
            assertNull(status.view)
        }
    }

    fun testSignedOutHidesTheWidget() {
        StubServer().use { srv ->
            val root = initRepo()
            signIn(srv)
            seedWorkspace()
            openInEditor(File(root, "envs/prod/main.tf"))

            val status = EditorStatus.getInstance(project)
            offEdt { status.refresh() }
            waitUntil { status.current?.ws?.name == "prod" }
            assertEquals("prod", status.current?.ws?.name)

            offEdt { session.signOut() }
            offEdt { status.refresh() }
            waitUntil { status.view == null }

            assertNull(status.view)
        }
    }

    /** [EditorStatus.planCurrent] itself runs on the EDT (like [RunActions.trigger], which asserts
     *  it) — called directly here, the same way `RunActionsGuardsTest` calls `RunActions.trigger`
     *  directly, not via [offEdt]. The branch-choice popup is bypassed via the
     *  [EditorStatus.branchChooser] test seam (real Swing popups aren't driven headlessly here —
     *  see the task-2 fix-round-1 report) but everything downstream — the git re-probe, the
     *  pin-then-trigger HTTP sequence — is the real production path against a real [StubServer]. */
    fun testPlanCurrentPinsToTheCheckedOutBranchWhenThatChoiceIsMade() {
        StubServer().use { srv ->
            val root = initRepo()
            checkoutBranch(root, "feature/x")
            signIn(srv)
            Store.getInstance().setSnapshotForTest(
                listOf(
                    Workspace(
                        id = "w1",
                        name = "prod",
                        tf_working_dir = "envs/prod",
                        repo_url = "https://github.com/acme/infra",
                        repo_ref = "main",
                    ),
                ),
                emptyList(),
            )
            srv.json("PUT", "/api/v1/workspaces/w1", 200, """{"id":"w1","name":"prod","repo_ref":"feature/x"}""")
            srv.json("POST", "/api/v1/workspaces/w1/runs", 200, """{"id":"r1","workspace_id":"w1","command":"plan","status":"pending"}""")

            openInEditor(File(root, "envs/prod/main.tf"))
            val status = EditorStatus.getInstance(project)
            offEdt { status.refresh() }
            waitUntil { status.current?.ws?.name == "prod" }

            status.branchChooser = { branch, _, onChosen -> onChosen(branch) } // "Plan on feature/x (pins the workspace)"
            status.planCurrent()

            waitUntil { srv.calls("POST", "/api/v1/workspaces/w1/runs").isNotEmpty() }
            assertTrue("expected a PUT to pin the workspace first", srv.calls("PUT", "/api/v1/workspaces/w1").isNotEmpty())
            val putIndex = srv.calls.indexOfFirst { it.method == "PUT" && it.path == "/api/v1/workspaces/w1" }
            val postIndex = srv.calls.indexOfFirst { it.method == "POST" && it.path == "/api/v1/workspaces/w1/runs" }
            assertTrue("the pin must land before the run is triggered", putIndex in 0 until postIndex)
        }
    }

    fun testPlanCurrentDoesNotPinWhenTheTrackedBranchIsChosen() {
        StubServer().use { srv ->
            val root = initRepo()
            checkoutBranch(root, "feature/x")
            signIn(srv)
            Store.getInstance().setSnapshotForTest(
                listOf(
                    Workspace(
                        id = "w1",
                        name = "prod",
                        tf_working_dir = "envs/prod",
                        repo_url = "https://github.com/acme/infra",
                        repo_ref = "main",
                    ),
                ),
                emptyList(),
            )
            // No PUT stub: if planCurrent() wrongly pinned here, the request would 404 and the
            // POST below would never be reached — failing the test loudly instead of silently.
            srv.json("POST", "/api/v1/workspaces/w1/runs", 200, """{"id":"r1","workspace_id":"w1","command":"plan","status":"pending"}""")

            openInEditor(File(root, "envs/prod/main.tf"))
            val status = EditorStatus.getInstance(project)
            offEdt { status.refresh() }
            waitUntil { status.current?.ws?.name == "prod" }

            status.branchChooser = { _, _, onChosen -> onChosen(null) } // "Plan on main"
            status.planCurrent()

            waitUntil { srv.calls("POST", "/api/v1/workspaces/w1/runs").isNotEmpty() }
            assertTrue("must not pin when the tracked branch was chosen", srv.calls("PUT", "/api/v1/workspaces/w1").isEmpty())
        }
    }
}
