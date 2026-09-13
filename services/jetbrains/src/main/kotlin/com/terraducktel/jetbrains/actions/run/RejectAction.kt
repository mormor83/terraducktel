package com.terraducktel.jetbrains.actions.run

import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.ui.Messages
import com.terraducktel.jetbrains.actions.ActionUtil
import com.terraducktel.jetbrains.output.RunActions
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.state.Store
import com.terraducktel.jetbrains.toolwindow.TdtDataKeys

/** Rejects the selected run after an optional reason prompt. Visible only for a run that is
 *  `awaiting_approval` and only for a session that can write. Port of VS Code's
 *  `terraducktel.rejectRun`. */
class RejectAction : AnAction("Reject…") {
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun update(e: AnActionEvent) {
        val run = e.getData(TdtDataKeys.RUN)
        e.presentation.isEnabledAndVisible =
            run != null && run.status == "awaiting_approval" && TdtSession.getInstance().canWrite()
    }

    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        val run = e.getData(TdtDataKeys.RUN) ?: return
        val wsName = RunActions.wsName(run)
        val reason = Messages.showInputDialog(
            project,
            "Reject ${run.command} on $wsName — reason (optional)",
            "Reject run",
            null,
        ) ?: return // cancelled

        ActionUtil.runBackground(project, "TDT: rejecting…") {
            val client = TdtSession.getInstance().requireClient()
            client.reject(run.id, reason.takeIf { it.isNotBlank() })
            Store.getInstance().refreshAndWait()
            ActionUtil.notify(project, "TDT: rejected $wsName ${run.command}.")
        }
    }
}
