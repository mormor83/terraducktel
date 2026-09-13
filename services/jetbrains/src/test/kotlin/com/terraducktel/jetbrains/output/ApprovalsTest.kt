package com.terraducktel.jetbrains.output

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.Messages
import com.intellij.testFramework.PlatformTestUtil
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.terraducktel.jetbrains.api.GraphSummary
import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.auth.PasswordSafeSecretStore
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.settings.Profile
import com.terraducktel.jetbrains.settings.TdtSettings
import com.terraducktel.jetbrains.testutil.InMemorySecretStore
import com.terraducktel.jetbrains.testutil.StubServer
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Platform coverage of [Approvals.approve]'s gate — the reason the review approved Task 11: an
 * operator must never end up with an approve POSTed unless they explicitly picked "Approve" in the
 * confirmation modal. Exercises the real [Approvals.approve] (background `getGraph` fetch, EDT
 * decision, and — were it ever wrongly reached — a background `approve` POST) against a
 * [StubServer], swapping the decision itself via the [Approvals.confirm] test seam.
 *
 * [Approvals.confirm] was chosen over [Messages.setTestDialog]: that hook is documented against
 * `Messages.showYesNoDialog`'s two-way `TestDialog` return convention, and was not verified to
 * intercept [com.intellij.openapi.ui.MessageDialogBuilder.YesNoCancel]'s three-way result in this
 * platform version — the seam gives a deterministic answer without depending on that.
 */
class ApprovalsTest : BasePlatformTestCase() {

    private val session get() = TdtSession.getInstance()
    private lateinit var secretStore: InMemorySecretStore
    private lateinit var originalConfirm: (Project, Run, GraphSummary) -> Int

    override fun setUp() {
        super.setUp()
        secretStore = InMemorySecretStore()
        session.secretStoreFactory = { secretStore }
        originalConfirm = Approvals.confirm
    }

    override fun tearDown() {
        try {
            Approvals.confirm = originalConfirm
            offEdt { session.signOut() }
            TdtSettings.getInstance().loadState(TdtSettings.State())
            session.secretStoreFactory = { PasswordSafeSecretStore() }
            offEdt { session.reload() }
            // reload()/signOut() publish sessionChanged via invokeLater — drain it now (same
            // reasoning as TdtSessionTest/TreesTest's tearDown).
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

    /** [Approvals.approve] hops background (fetch the graph summary) -> EDT (the confirm seam) ->
     *  maybe background again (the approve POST, if the gate is ever broken) — so asserting right
     *  after calling `approve()` would race a still-in-flight fetch. Pumps the EDT queue (so the
     *  posted `invokeLater` can run) until [reached] is true or [timeoutMs] elapses. */
    private fun waitUntil(timeoutMs: Long = 5_000, reached: () -> Boolean) {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (!reached() && System.currentTimeMillis() < deadline) {
            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
            Thread.sleep(10)
        }
        assertTrue("timed out waiting for Approvals.approve's confirm seam to be reached", reached())
    }

    private val run = Run(id = "r1", workspace_id = "w1", command = "apply", status = "awaiting_approval")

    fun `test cancelling the confirm dialog never posts approve`() {
        StubServer().use { srv ->
            srv.json("GET", "/api/v1/runs/r1/graph", 200, """{"nodes":[],"edges":[],"summary":{"add":1,"change":0,"destroy":0,"replace":0}}""")
            srv.json("POST", "/api/v1/runs/r1/approve", 200, "{}")
            setProfile(srv)
            offEdt { session.reload() }
            offEdt { session.signInWithApiKey("tdt_x") }

            val confirmed = AtomicBoolean(false)
            Approvals.confirm = { _, _, _ -> confirmed.set(true); Messages.CANCEL }

            Approvals.approve(project, run)

            waitUntil { confirmed.get() }
            // Give a (wrongly) queued approve POST a moment to actually land before asserting its
            // absence, pumping the EDT the whole time in case it needs another hop.
            val deadline = System.currentTimeMillis() + 300
            while (System.currentTimeMillis() < deadline) {
                PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
                Thread.sleep(10)
            }
            assertTrue("approve must never be POSTed after Cancel", srv.calls("POST", "/api/v1/runs/r1/approve").isEmpty())
        }
    }

    fun `test choosing show-plan (No) never posts approve either`() {
        StubServer().use { srv ->
            srv.json("GET", "/api/v1/runs/r1/graph", 200, """{"nodes":[],"edges":[],"summary":{"add":0,"change":0,"destroy":0,"replace":0}}""")
            srv.json("GET", "/api/v1/runs/r1/plan", 200, """{"plan_output":"+ a\n"}""")
            srv.json("POST", "/api/v1/runs/r1/approve", 200, "{}")
            setProfile(srv)
            offEdt { session.reload() }
            offEdt { session.signInWithApiKey("tdt_x") }

            val confirmed = AtomicBoolean(false)
            Approvals.confirm = { _, _, _ -> confirmed.set(true); Messages.NO }

            Approvals.approve(project, run)

            waitUntil { confirmed.get() }
            val deadline = System.currentTimeMillis() + 300
            while (System.currentTimeMillis() < deadline) {
                PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
                Thread.sleep(10)
            }
            assertTrue("approve must never be POSTed after Show plan", srv.calls("POST", "/api/v1/runs/r1/approve").isEmpty())
        }
    }
}
