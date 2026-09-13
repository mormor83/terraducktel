package com.terraducktel.jetbrains.actions.run

import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.terraducktel.jetbrains.output.Approvals
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.toolwindow.TdtDataKeys

/** Approves the selected run, after the gated confirmation modal ([Approvals.approve]). Visible
 *  only for a run that is actually `awaiting_approval` and only for a session that can write. Port
 *  of VS Code's `terraducktel.approveRun`. */
class ApproveAction : AnAction("Approve…") {
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun update(e: AnActionEvent) {
        val run = e.getData(TdtDataKeys.RUN)
        e.presentation.isEnabledAndVisible =
            run != null && run.status == "awaiting_approval" && TdtSession.getInstance().canWrite()
    }

    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        val run = e.getData(TdtDataKeys.RUN) ?: return
        Approvals.approve(project, run)
    }
}
