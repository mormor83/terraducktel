package com.terraducktel.jetbrains.actions.run

import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.terraducktel.jetbrains.actions.ActionUtil
import com.terraducktel.jetbrains.api.CANCELLABLE_RUN_STATUSES
import com.terraducktel.jetbrains.output.RunActions
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.state.Store
import com.terraducktel.jetbrains.toolwindow.TdtDataKeys

/** Requests cancellation of the selected run. Visible only while the run is in one of
 *  [CANCELLABLE_RUN_STATUSES] and only for a session that can write. Port of VS Code's
 *  `terraducktel.cancelRun`. */
class CancelRunAction : AnAction("Cancel Run") {
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun update(e: AnActionEvent) {
        val run = e.getData(TdtDataKeys.RUN)
        e.presentation.isEnabledAndVisible =
            run != null && run.status in CANCELLABLE_RUN_STATUSES && TdtSession.getInstance().canWrite()
    }

    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        val run = e.getData(TdtDataKeys.RUN) ?: return
        val wsName = RunActions.wsName(run)
        ActionUtil.runBackground(project, "TDT: cancelling…") {
            val client = TdtSession.getInstance().requireClient()
            client.cancel(run.id)
            Store.getInstance().refreshAndWait()
            ActionUtil.notify(project, "TDT: cancel requested for $wsName ${run.command}.")
        }
    }
}
