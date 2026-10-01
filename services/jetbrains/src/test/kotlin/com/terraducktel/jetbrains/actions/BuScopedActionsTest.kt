package com.terraducktel.jetbrains.actions

import com.intellij.openapi.application.ApplicationManager
import com.intellij.testFramework.PlatformTestUtil
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.terraducktel.jetbrains.actions.run.CancelRunAction
import com.terraducktel.jetbrains.actions.run.RejectAction
import com.terraducktel.jetbrains.actions.workspace.SetBranchAction
import com.terraducktel.jetbrains.actions.workspace.SyncWorkspaceAction
import com.terraducktel.jetbrains.api.BusinessUnit
import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.api.Workspace
import com.terraducktel.jetbrains.auth.PasswordSafeSecretStore
import com.terraducktel.jetbrains.output.PlanDocument
import com.terraducktel.jetbrains.output.RunActions
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.settings.Profile
import com.terraducktel.jetbrains.settings.TdtSettings
import com.terraducktel.jetbrains.state.BuState
import com.terraducktel.jetbrains.state.RunRef
import com.terraducktel.jetbrains.state.Store
import com.terraducktel.jetbrains.testutil.InMemorySecretStore
import com.terraducktel.jetbrains.testutil.StubServer
import com.terraducktel.jetbrains.toolwindow.TdtDataKeys
import java.util.concurrent.TimeUnit

/**
 * Every action on a workspace or run must be issued through a client bound to THE NODE'S business
 * unit (`client.withBu(node.bu)`), never a session-wide "current" one — so each test points a
 * [StubServer] at the real [TdtSession], fires the action's entry point with a BU slug, and asserts
 * the `X-Business-Unit` header the server actually received.
 */
class BuScopedActionsTest : BasePlatformTestCase() {

    private val session get() = TdtSession.getInstance()
    private lateinit var originalClientProvider: () -> com.terraducktel.jetbrains.api.TdtClient?

    override fun setUp() {
        super.setUp()
        session.secretStoreFactory = { InMemorySecretStore() }
        // Keep the store's own background polling out of the call log these tests assert on.
        originalClientProvider = Store.getInstance().clientProvider
        Store.getInstance().clientProvider = { null }
    }

    override fun tearDown() {
        try {
            Store.getInstance().clientProvider = originalClientProvider
            offEdt { session.signOut() }
            TdtSettings.getInstance().loadState(TdtSettings.State())
            session.secretStoreFactory = { PasswordSafeSecretStore() }
            offEdt { session.reload() }
            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
        } finally {
            super.tearDown()
        }
    }

    private fun <T> offEdt(block: () -> T): T =
        ApplicationManager.getApplication().executeOnPooledThread<T> { block() }.get(10, TimeUnit.SECONDS)

    private fun signIn(srv: StubServer) {
        TdtSettings.getInstance().state.profiles = mutableListOf(Profile(name = "p", url = srv.url))
        TdtSettings.getInstance().state.activeProfile = "p"
        offEdt { session.reload() }
        offEdt { session.signInWithApiKey("tdt_x") }
    }

    private fun waitUntil(timeoutMs: Long = 5_000, reached: () -> Boolean) {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (!reached() && System.currentTimeMillis() < deadline) {
            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
            Thread.sleep(10)
        }
        assertTrue("timed out waiting for the request", reached())
    }

    private val ws = Workspace(id = "w1", name = "prod-vpc", repo_ref = "main")
    private val run = Run(id = "r1", workspace_id = "w1", command = "plan", status = "running")

    fun `test requireClient for a business unit sends that BU's header`() {
        StubServer().use { srv ->
            srv.json("GET", "/api/v1/workspaces", 200, "[]")
            signIn(srv)

            offEdt { session.requireClient("ops").listWorkspaces() }

            assertEquals("ops", srv.calls("GET", "/api/v1/workspaces").single().headers["x-business-unit"])
            assertNull("the session-wide client itself is BU-less", session.client!!.bu.takeIf { it.isNotEmpty() })
        }
    }

    fun `test sync is issued against the workspace's business unit`() {
        StubServer().use { srv ->
            srv.json("POST", "/api/v1/workspaces/w1/sync", 200, "{}")
            signIn(srv)

            SyncWorkspaceAction.sync(project, "apps", ws)

            waitUntil { srv.calls("POST", "/api/v1/workspaces/w1/sync").isNotEmpty() }
            assertEquals("apps", srv.calls("POST", "/api/v1/workspaces/w1/sync").single().headers["x-business-unit"])
        }
    }

    fun `test cancel is issued against the run's business unit`() {
        StubServer().use { srv ->
            srv.json("POST", "/api/v1/runs/r1/cancel", 200, "{}")
            signIn(srv)

            CancelRunAction.cancel(project, "apps", run)

            waitUntil { srv.calls("POST", "/api/v1/runs/r1/cancel").isNotEmpty() }
            assertEquals("apps", srv.calls("POST", "/api/v1/runs/r1/cancel").single().headers["x-business-unit"])
        }
    }

    fun `test reject is issued against the run's business unit`() {
        StubServer().use { srv ->
            srv.json("POST", "/api/v1/runs/r1/reject", 200, "{}")
            signIn(srv)

            RejectAction.submit(project, "apps", run, "not today")

            waitUntil { srv.calls("POST", "/api/v1/runs/r1/reject").isNotEmpty() }
            assertEquals("apps", srv.calls("POST", "/api/v1/runs/r1/reject").single().headers["x-business-unit"])
        }
    }

    fun `test setting the tracked branch is issued against the workspace's business unit`() {
        StubServer().use { srv ->
            srv.json("PUT", "/api/v1/workspaces/w1", 200, """{"id":"w1","name":"prod-vpc"}""")
            signIn(srv)

            SetBranchAction.applyBranch(project, "apps", ws, "feature-x")

            waitUntil { srv.calls("PUT", "/api/v1/workspaces/w1").isNotEmpty() }
            assertEquals("apps", srv.calls("PUT", "/api/v1/workspaces/w1").single().headers["x-business-unit"])
        }
    }

    fun `test opening a plan is issued against the run's business unit`() {
        StubServer().use { srv ->
            srv.json("GET", "/api/v1/runs/r1/plan", 200, """{"plan_output":"+ a\n"}""")
            signIn(srv)

            PlanDocument.open(project, "apps", "r1", "prod-vpc")

            waitUntil { srv.calls("GET", "/api/v1/runs/r1/plan").isNotEmpty() }
            assertEquals("apps", srv.calls("GET", "/api/v1/runs/r1/plan").single().headers["x-business-unit"])
        }
    }

    fun `test the run chooser labels name each run's business unit`() {
        val infra = BusinessUnit("1", "infra", "Infra")
        val apps = BusinessUnit("2", "apps", "Apps")
        Store.getInstance().setSnapshotForTest(
            listOf(
                BuState(infra, listOf(ws), listOf(run), loaded = true),
                BuState(apps, listOf(Workspace(id = "w2", name = "prod-vpc")), listOf(Run("r2", "w2", "apply", "planned")), loaded = true),
            ),
        )
        try {
            val labels = RunActions.chooserLabels(Store.getInstance().allRuns())

            assertEquals(2, labels.toSet().size)
            assertTrue(labels.any { it.endsWith("(Infra)") })
            assertTrue(labels.any { it.endsWith("(Apps)") })
            assertEquals(RunRef(run, infra), Store.getInstance().findRun("r1"))
        } finally {
            Store.getInstance().setSnapshotForTest(emptyList(), emptyList())
        }
    }

    fun `test the data key for the node's business unit is stable`() {
        assertEquals("terraducktel.bu", TdtDataKeys.BU.name)
    }
}
