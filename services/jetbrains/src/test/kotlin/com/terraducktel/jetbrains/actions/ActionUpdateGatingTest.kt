package com.terraducktel.jetbrains.actions

import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.DataContext
import com.intellij.testFramework.TestActionEvent
import com.intellij.testFramework.fixtures.BasePlatformTestCase
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
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.toolwindow.TdtDataKeys

/**
 * The plugin's whole client-side RBAC story lives in each action's `update()`: every write action
 * must hide itself for a session that [TdtSession.canWrite] says cannot write, and Approve/Reject/
 * Cancel additionally gate on the selected run's status. Drives `update()` directly via
 * [TestActionEvent.createTestEvent] with a [DataContext] supplying [TdtDataKeys.WORKSPACE]/
 * [TdtDataKeys.RUN] — no tool window, no tree, no real sign-in needed.
 *
 * [TdtSession.canWriteProvider] is the seam that makes `canWrite()` deterministic here, the same
 * idea as [com.terraducktel.jetbrains.output.Approvals.confirm] / [com.terraducktel.jetbrains
 * .output.RunActions.confirmApply]: driving a real sign-in (password/API key/SSO, each with its
 * own claims-derived role) just to flip one boolean would make this test slow and roundabout for
 * no extra coverage — `canWrite()`'s own claims-to-boolean logic isn't what this test is about.
 *
 * Covers all eight write actions (Plan/Apply/Destroy/SetBranch/Sync/Approve/Reject/Cancel) for the
 * `canWrite() == false` case, and the visible/invisible split each of Approve/Reject/Cancel adds.
 */
class ActionUpdateGatingTest : BasePlatformTestCase() {

    private lateinit var originalCanWriteProvider: () -> Boolean

    override fun setUp() {
        super.setUp()
        originalCanWriteProvider = TdtSession.getInstance().canWriteProvider
    }

    override fun tearDown() {
        try {
            TdtSession.getInstance().canWriteProvider = originalCanWriteProvider
        } finally {
            super.tearDown()
        }
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

    fun `test every write action is invisible when canWrite is false`() {
        TdtSession.getInstance().canWriteProvider = { false }
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

    fun `test workspace actions are visible when canWrite is true and a workspace is selected`() {
        TdtSession.getInstance().canWriteProvider = { true }
        val wsContext = contextFor(ws = ws)

        assertTrue(visible(PlanAction(), wsContext))
        assertTrue(visible(ApplyAction(), wsContext))
        assertTrue(visible(DestroyAction(), wsContext))
        assertTrue(visible(SetBranchAction(), wsContext))
        assertTrue(visible(SyncWorkspaceAction(), wsContext))
    }

    fun `test Approve and Reject are visible only for a run awaiting approval`() {
        TdtSession.getInstance().canWriteProvider = { true }

        assertTrue(visible(ApproveAction(), contextFor(run = awaitingRun)))
        assertTrue(visible(RejectAction(), contextFor(run = awaitingRun)))
        assertFalse(visible(ApproveAction(), contextFor(run = plannedRun)))
        assertFalse(visible(RejectAction(), contextFor(run = plannedRun)))
    }

    fun `test Cancel is visible only for a cancellable run status`() {
        TdtSession.getInstance().canWriteProvider = { true }

        assertTrue(visible(CancelRunAction(), contextFor(run = runningRun)))
        assertTrue(visible(CancelRunAction(), contextFor(run = awaitingRun)))
        assertFalse(visible(CancelRunAction(), contextFor(run = plannedRun)))
    }
}
