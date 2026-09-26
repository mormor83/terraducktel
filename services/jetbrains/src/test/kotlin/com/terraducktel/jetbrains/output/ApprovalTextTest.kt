package com.terraducktel.jetbrains.output

import com.intellij.openapi.ui.Messages
import com.terraducktel.jetbrains.api.GraphSummary
import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.notifications.ApprovalNotice
import com.terraducktel.jetbrains.notifications.ApprovalNotifier
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

/** Copy shown by the Approve dialog and the approval balloon (docs/design_handoff_ide_plugins). */
class ApprovalTextTest {

    private val run = Run(id = "r1", workspace_id = "w1", command = "apply", status = "awaiting_approval")

    @Test fun `summary spells out every count and omits replace when zero`() {
        assertEquals("+2 to add, ~1 to change, -2 to destroy, ±1 to replace", Approvals.summaryText(GraphSummary(2, 1, 2, 1)))
        assertEquals("+2 to add, ~1 to change, -2 to destroy", Approvals.summaryText(GraphSummary(2, 1, 2, 0)))
    }

    @Test fun `dialog title, body and three buttons`() {
        assertEquals("Approve apply on worker-pool?", Approvals.dialogTitle(run, "worker-pool"))
        assertEquals("+2 to add, ~1 to change, -2 to destroy\n\nNothing is applied until you click Approve.", Approvals.dialogMessage(GraphSummary(2, 1, 2, 0)))
        assertEquals(listOf("Approve", "Show plan", "Cancel"), Approvals.DIALOG_BUTTONS.toList())
    }

    @Test fun `dialog button index maps onto the YES-NO-CANCEL contract of the confirm seam`() {
        assertEquals(Messages.YES, Approvals.choiceFor(0))
        assertEquals(Messages.NO, Approvals.choiceFor(1))
        assertEquals(Messages.CANCEL, Approvals.choiceFor(2))
        assertEquals(Messages.CANCEL, Approvals.choiceFor(-1)) // dialog closed with Esc / the window close button
    }

    @Test fun `balloon body is wrapping HTML - sentence, line break, summary`() {
        val body = ApprovalNotifier.body(ApprovalNotice(run, "worker-pool", GraphSummary(2, 1, 2, 1)))
        assertEquals("<html>TDT: worker-pool apply awaits approval<br>+2 to add, ~1 to change, -2 to destroy, ±1 to replace</html>", body)
        assertFalse(body.contains("nowrap"))
    }

    @Test fun `balloon body has no counts when the summary is unknown, and escapes the workspace name`() {
        assertEquals("<html>TDT: a&lt;b&gt; apply awaits approval</html>", ApprovalNotifier.body(ApprovalNotice(run, "a<b>", null)))
        assertEquals("Terraducktel approvals", ApprovalNotifier.TITLE)
    }
}
