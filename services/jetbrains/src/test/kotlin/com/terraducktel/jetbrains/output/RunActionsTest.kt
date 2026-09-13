package com.terraducktel.jetbrains.output

import com.terraducktel.jetbrains.api.Workspace
import org.junit.Assert.*
import org.junit.Test

/**
 * Plain-JUnit coverage of [RunActions]'s pure decision helpers — [RunActions.triggerPlanFor] and
 * [RunActions.pinFailedMessage] — extracted so the pin / no-pin / `branch == repo_ref` cases and
 * the pin-succeeded-but-trigger-failed error wrapping can be asserted directly, without any
 * platform machinery (the surrounding [RunActions.trigger] drives `MessageDialogBuilder`/
 * `Messages` dialogs and a background `TdtClient` call, none of which are exercised here — see
 * the task-10 report for why a full platform test of `trigger`/`RunConsoles` was skipped).
 */
class RunActionsTest {
    private fun ws(repoRef: String) = Workspace(id = "w1", name = "prod-vpc", repo_ref = repoRef)

    @Test fun `no branch given means no pin`() {
        val plan = RunActions.triggerPlanFor(ws("main"), "plan", branch = null)
        assertNull(plan.pin)
        assertEquals("plan", plan.body.command)
        assertNull(plan.body.branch)
    }

    @Test fun `branch equal to the tracked ref means no pin`() {
        val plan = RunActions.triggerPlanFor(ws("main"), "apply", branch = "main")
        assertNull(plan.pin)
    }

    @Test fun `branch different from the tracked ref pins it`() {
        val plan = RunActions.triggerPlanFor(ws("main"), "plan", branch = "feature-x")
        assertEquals("feature-x", plan.pin)
        assertEquals("plan", plan.body.command)
    }

    @Test fun `pin failure message reports both the pin and the trigger failure`() {
        val msg = RunActions.pinFailedMessage("feature-x", "apply", IllegalStateException("workspace is locked"))
        assertEquals("pinned to feature-x, but apply failed: workspace is locked", msg)
    }
}
