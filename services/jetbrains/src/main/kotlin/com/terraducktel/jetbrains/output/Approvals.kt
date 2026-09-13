package com.terraducktel.jetbrains.output

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.MessageDialogBuilder
import com.intellij.openapi.ui.Messages
import com.terraducktel.jetbrains.actions.ActionUtil
import com.terraducktel.jetbrains.api.GraphSummary
import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.state.Store

/**
 * The gated-approval modal shared by every caller that can approve a run: the Runs tree context
 * menu's `Terraducktel.Approve` action, the awaiting-approval balloon's "Approve…" action (wired
 * via [RunActions.approveHook]), and Plan 2's approval notifications. A port of `services/vscode/
 * src/commands/run.ts`'s `approve` command.
 */
object Approvals {

    /** Loads the plan's add/change/destroy/replace summary (best-effort — a failed fetch shows the
     *  dialog with all-zero counts rather than blocking the approval) then, on the EDT, asks
     *  Approve / Show plan / Cancel. Must be called on the EDT; the network calls run in
     *  background tasks. */
    fun approve(project: Project, run: Run) {
        val wsName = RunActions.wsName(run)
        ActionUtil.runBackground(project, "TDT: loading plan summary…") {
            val summary = try {
                TdtSession.getInstance().requireClient().getGraph(run.id).summary
            } catch (e: Exception) {
                GraphSummary()
            }
            ApplicationManager.getApplication().invokeLater {
                val choice = MessageDialogBuilder.yesNoCancel(
                    "Approve ${run.command} on $wsName?",
                    "+${summary.add} to add, ~${summary.change} to change, -${summary.destroy} to destroy, " +
                        "±${summary.replace} to replace.",
                )
                    .yesText("Approve")
                    .noText("Show plan")
                    .cancelText("Cancel")
                    .asWarning()
                    .show(project)
                when (choice) {
                    Messages.YES -> doApprove(project, run, wsName)
                    Messages.NO -> PlanDocument.open(project, run.id, wsName)
                    else -> Unit
                }
            }
        }
    }

    private fun doApprove(project: Project, run: Run, wsName: String) {
        ActionUtil.runBackground(project, "TDT: approving…") {
            TdtSession.getInstance().requireClient().approve(run.id)
            ActionUtil.notify(project, "TDT: approved $wsName ${run.command}.")
            Store.getInstance().refreshAndWait()
            ApplicationManager.getApplication().invokeLater {
                RunActions.watch(project, run)
            }
        }
    }
}
