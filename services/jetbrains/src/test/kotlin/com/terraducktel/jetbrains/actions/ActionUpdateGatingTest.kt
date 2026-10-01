package com.terraducktel.jetbrains.actions

import com.intellij.openapi.actionSystem.ActionManager
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.DataContext
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.util.Disposer
import com.intellij.testFramework.PlatformTestUtil
import com.intellij.testFramework.TestActionEvent
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.terraducktel.jetbrains.actions.auth.FilterBusinessUnitsAction
import com.terraducktel.jetbrains.actions.run.ApproveAction
import com.terraducktel.jetbrains.actions.run.CancelRunAction
import com.terraducktel.jetbrains.actions.run.RejectAction
import com.terraducktel.jetbrains.actions.workspace.ApplyAction
import com.terraducktel.jetbrains.actions.workspace.DestroyAction
import com.terraducktel.jetbrains.actions.workspace.PlanAction
import com.terraducktel.jetbrains.actions.workspace.SetBranchAction
import com.terraducktel.jetbrains.actions.workspace.SyncWorkspaceAction
import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.api.Workspace
import com.terraducktel.jetbrains.auth.PasswordSafeSecretStore
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.settings.Profile
import com.terraducktel.jetbrains.settings.TdtSettings
import com.terraducktel.jetbrains.testutil.InMemorySecretStore
import com.terraducktel.jetbrains.testutil.StubServer
import com.terraducktel.jetbrains.toolwindow.TdtDataKeys
import java.util.Base64
import java.util.concurrent.TimeUnit

/**
 * Write actions are gated on being signed in, never on a role: roles are per business unit, so the
 * token's global (legacy) role says nothing about what the user may do in a given BU — the server
 * (`require_role` + per-BU membership) is the only authority and a refusal surfaces as its own
 * error message. Approve/Reject/Cancel additionally gate on the selected run's status. Drives
 * `update()` directly via [TestActionEvent.createTestEvent] with a [DataContext] supplying
 * [TdtDataKeys.WORKSPACE]/[TdtDataKeys.RUN], after really signing in (API key, or a password
 * sign-in whose JWT claims a given global role) against a [StubServer].
 */
class ActionUpdateGatingTest : BasePlatformTestCase() {

    private val session get() = TdtSession.getInstance()

    override fun setUp() {
        super.setUp()
        session.secretStoreFactory = { InMemorySecretStore() }
    }

    override fun tearDown() {
        try {
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

    private fun fakeJwt(role: String): String {
        val payload = Base64.getUrlEncoder().withoutPadding()
            .encodeToString("""{"role":"$role","is_superadmin":false}""".toByteArray(Charsets.UTF_8))
        return "h.$payload.s"
    }

    private fun setProfile(srv: StubServer) {
        TdtSettings.getInstance().state.profiles = mutableListOf(Profile(name = "p", url = srv.url))
        TdtSettings.getInstance().state.activeProfile = "p"
        offEdt { session.reload() }
    }

    private fun signInAsGlobalRole(srv: StubServer, role: String) {
        srv.json("POST", "/api/v1/auth/token", 200, """{"access_token":"${fakeJwt(role)}","refresh_token":"r"}""")
        setProfile(srv)
        offEdt { session.signInWithPassword("a@b", "pw") }
    }

    private val ws = Workspace(id = "w1", name = "prod-vpc")
    private val awaitingRun = Run(id = "r1", workspace_id = "w1", command = "apply", status = "awaiting_approval")
    private val runningRun = Run(id = "r2", workspace_id = "w1", command = "apply", status = "running")
    private val plannedRun = Run(id = "r3", workspace_id = "w1", command = "plan", status = "planned")

    private fun contextFor(ws: Workspace? = null, run: Run? = null): DataContext = DataContext { dataId ->
        when {
            TdtDataKeys.WORKSPACE.`is`(dataId) -> ws
            TdtDataKeys.RUN.`is`(dataId) -> run
            else -> null
        }
    }

    private fun visible(action: AnAction, context: DataContext): Boolean {
        val event = TestActionEvent.createTestEvent(action, context)
        action.update(event)
        return event.presentation.isEnabledAndVisible
    }

    private fun signedIn() {
        val srv = StubServer()
        testRootDisposable.let { Disposer.register(it) { srv.close() } }
        setProfile(srv)
        offEdt { session.signInWithApiKey("tdt_x") }
    }

    private fun assertAllWriteActionsVisible() {
        val wsContext = contextFor(ws = ws)
        assertTrue(visible(PlanAction(), wsContext))
        assertTrue(visible(ApplyAction(), wsContext))
        assertTrue(visible(DestroyAction(), wsContext))
        assertTrue(visible(SetBranchAction(), wsContext))
        assertTrue(visible(SyncWorkspaceAction(), wsContext))
        assertTrue(visible(ApproveAction(), contextFor(run = awaitingRun)))
        assertTrue(visible(RejectAction(), contextFor(run = awaitingRun)))
        assertTrue(visible(CancelRunAction(), contextFor(run = runningRun)))
    }

    fun `test every write action is invisible while signed out`() {
        val wsContext = contextFor(ws = ws)

        assertFalse(visible(PlanAction(), wsContext))
        assertFalse(visible(ApplyAction(), wsContext))
        assertFalse(visible(DestroyAction(), wsContext))
        assertFalse(visible(SetBranchAction(), wsContext))
        assertFalse(visible(SyncWorkspaceAction(), wsContext))
        assertFalse(visible(ApproveAction(), contextFor(run = awaitingRun)))
        assertFalse(visible(RejectAction(), contextFor(run = awaitingRun)))
        assertFalse(visible(CancelRunAction(), contextFor(run = runningRun)))
    }

    fun `test a signed-in user whose token claims the viewer role still sees every write action`() {
        StubServer().use { srv ->
            signInAsGlobalRole(srv, "viewer")
            assertAllWriteActionsVisible()
        }
    }

    fun `test a signed-in operator and an API key session see every write action`() {
        StubServer().use { srv ->
            signInAsGlobalRole(srv, "operator")
            assertAllWriteActionsVisible()
            offEdt { session.signOut() }
            offEdt { session.signInWithApiKey("tdt_x") }
            assertAllWriteActionsVisible()
        }
    }

    fun `test Approve and Reject are visible only for a run awaiting approval`() {
        signedIn()

        assertTrue(visible(ApproveAction(), contextFor(run = awaitingRun)))
        assertTrue(visible(RejectAction(), contextFor(run = awaitingRun)))
        assertFalse(visible(ApproveAction(), contextFor(run = plannedRun)))
        assertFalse(visible(RejectAction(), contextFor(run = plannedRun)))
    }

    fun `test Cancel is visible only for a cancellable run status`() {
        signedIn()

        assertTrue(visible(CancelRunAction(), contextFor(run = runningRun)))
        assertTrue(visible(CancelRunAction(), contextFor(run = awaitingRun)))
        assertFalse(visible(CancelRunAction(), contextFor(run = plannedRun)))
    }

    fun `test the business unit filter replaces the old business unit switcher`() {
        val manager = ActionManager.getInstance()
        assertNotNull(manager.getAction("Terraducktel.FilterBusinessUnits"))
        assertNull("the single-BU switcher is gone", manager.getAction("Terraducktel.SwitchBu"))
    }

    fun `test the business unit filter is disabled while signed out`() {
        val event = TestActionEvent.createTestEvent(FilterBusinessUnitsAction(), contextFor())
        FilterBusinessUnitsAction().update(event)
        assertFalse(event.presentation.isEnabled)
    }
}
