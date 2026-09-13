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
import org.junit.Assume
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
 * Skipped outright (via [Assume]) when `git` isn't on `PATH`: the whole point of this suite is
 * exercising the real `git rev-parse`/`remote get-url` calls [GitProbe] shells out to.
 */
class EditorStatusTest : BasePlatformTestCase() {

    private val session get() = TdtSession.getInstance()
    private lateinit var secretStore: InMemorySecretStore
    private lateinit var originalClientProvider: () -> TdtClient?
    private var repoRoot: File? = null

    override fun setUp() {
        super.setUp()
        Assume.assumeTrue("git is not on PATH", gitAvailable())
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

    fun testANonTerraformFileIsNeverMapped() {
        StubServer().use { srv ->
            val root = initRepo()
            signIn(srv)
            seedWorkspace()
            val py = File(root, "envs/prod/script.py").apply { writeText("print(1)\n") }
            openInEditor(py)

            val status = EditorStatus.getInstance(project)
            offEdt { status.refresh() }
            // Nothing will ever make this true; just give refresh() a bounded window to run.
            waitUntil(timeoutMs = 1_000) { false }

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
}
