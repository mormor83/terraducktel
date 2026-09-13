package com.terraducktel.jetbrains.output

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.project.Project
import com.intellij.testFramework.PlatformTestUtil
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.terraducktel.jetbrains.api.Workspace
import com.terraducktel.jetbrains.auth.PasswordSafeSecretStore
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.settings.Profile
import com.terraducktel.jetbrains.settings.TdtSettings
import com.terraducktel.jetbrains.testutil.InMemorySecretStore
import com.terraducktel.jetbrains.testutil.StubServer
import java.util.concurrent.TimeUnit

/**
 * Platform coverage of [RunActions.trigger]'s Apply confirmation and Destroy type-the-name gates —
 * the same treatment [ApprovalsTest] gives [Approvals.approve]: a declined confirmation must never
 * reach the network. Exercises the real [RunActions.trigger] (EDT confirmation seam, then a
 * background `POST .../runs`) against a [StubServer], swapping the decision itself via the
 * [RunActions.confirmApply]/[RunActions.confirmDestroy] test seams — the same rationale
 * [ApprovalsTest] documents for [Approvals.confirm].
 */
class RunActionsGuardsTest : BasePlatformTestCase() {

    private val session get() = TdtSession.getInstance()
    private lateinit var secretStore: InMemorySecretStore
    private lateinit var originalConfirmApply: (Project, Workspace) -> Boolean
    private lateinit var originalConfirmDestroy: (Project, Workspace) -> Boolean

    override fun setUp() {
        super.setUp()
        secretStore = InMemorySecretStore()
        session.secretStoreFactory = { secretStore }
        originalConfirmApply = RunActions.confirmApply
        originalConfirmDestroy = RunActions.confirmDestroy
    }

    override fun tearDown() {
        try {
            RunActions.confirmApply = originalConfirmApply
            RunActions.confirmDestroy = originalConfirmDestroy
            offEdt { session.signOut() }
            TdtSettings.getInstance().loadState(TdtSettings.State())
            session.secretStoreFactory = { PasswordSafeSecretStore() }
            offEdt { session.reload() }
            // reload()/signOut() publish sessionChanged via invokeLater — drain it now (same
            // reasoning as ApprovalsTest/TdtSessionTest's tearDown).
            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
        } finally {
            super.tearDown()
        }
    }

    private fun <T> offEdt(block: () -> T): T =
        ApplicationManager.getApplication().executeOnPooledThread<T> { block() }.get(10, TimeUnit.SECONDS)

    private fun setProfile(srv: StubServer, name: String = "p"): Profile {
        val profile = Profile(name = name, url = srv.url)
        TdtSettings.getInstance().state.profiles = mutableListOf(profile)
        TdtSettings.getInstance().state.activeProfile = name
        return profile
    }

    private fun ws() = Workspace(id = "w1", name = "prod-vpc", repo_ref = "main")

    private fun waitUntil(timeoutMs: Long = 5_000, reached: () -> Boolean) {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (!reached() && System.currentTimeMillis() < deadline) {
            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
            Thread.sleep(10)
        }
        assertTrue("timed out waiting for the run trigger", reached())
    }

    /** Pumps the EDT for a short grace period so a (wrongly) scheduled background POST has a
     *  chance to actually land before we assert its absence — mirrors [ApprovalsTest]'s "cancelling
     *  the confirm dialog never posts approve" test. */
    private fun assertNeverPosted(srv: StubServer, path: String) {
        val deadline = System.currentTimeMillis() + 300
        while (System.currentTimeMillis() < deadline) {
            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
            Thread.sleep(10)
        }
        assertTrue("must never POST $path after a declined confirmation", srv.calls("POST", path).isEmpty())
    }

    fun `test a declined destroy confirmation never posts a run`() {
        StubServer().use { srv ->
            setProfile(srv)
            offEdt { session.reload() }
            offEdt { session.signInWithApiKey("tdt_x") }

            RunActions.confirmDestroy = { _, _ -> false }

            RunActions.trigger(project, ws(), "destroy")

            assertNeverPosted(srv, "/api/v1/workspaces/w1/runs")
        }
    }

    fun `test a confirmed destroy posts a run`() {
        StubServer().use { srv ->
            srv.json("GET", "/api/v1/workspaces", 200, "[]")
            srv.json("GET", "/api/v1/runs", 200, "[]")
            srv.json("POST", "/api/v1/workspaces/w1/runs", 200, """{"id":"r1","workspace_id":"w1","command":"destroy","status":"pending"}""")
            setProfile(srv)
            offEdt { session.reload() }
            offEdt { session.signInWithApiKey("tdt_x") }

            RunActions.confirmDestroy = { _, _ -> true }

            RunActions.trigger(project, ws(), "destroy")

            waitUntil { srv.calls("POST", "/api/v1/workspaces/w1/runs").isNotEmpty() }
        }
    }

    fun `test a declined apply confirmation never posts a run`() {
        StubServer().use { srv ->
            setProfile(srv)
            offEdt { session.reload() }
            offEdt { session.signInWithApiKey("tdt_x") }

            RunActions.confirmApply = { _, _ -> false }

            RunActions.trigger(project, ws(), "apply")

            assertNeverPosted(srv, "/api/v1/workspaces/w1/runs")
        }
    }
}
