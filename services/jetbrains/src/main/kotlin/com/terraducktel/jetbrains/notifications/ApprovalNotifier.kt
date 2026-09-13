package com.terraducktel.jetbrains.notifications

import com.intellij.ide.BrowserUtil
import com.intellij.notification.NotificationAction
import com.intellij.notification.NotificationGroupManager
import com.intellij.notification.NotificationType
import com.intellij.openapi.project.Project
import com.terraducktel.jetbrains.actions.run.RejectAction
import com.terraducktel.jetbrains.output.Approvals
import com.terraducktel.jetbrains.session.TdtSession

/**
 * Turns one [ApprovalNotice] from [ApprovalWatcher]'s background poll into a sticky IDE balloon —
 * the background-poll half of the awaiting-approval story ([com.terraducktel.jetbrains.output.
 * RunActions.announceAwaiting] is the run-output-tail half, for a run this window itself started or
 * is watching). A port of `services/vscode/src/extension.ts`'s `showInformationMessage(...)`
 * handler for the same `ApprovalWatcher` event.
 *
 * Every action defers to the SAME gated flow the Runs tree's context menu uses —
 * [Approvals.approve] / [RejectAction.reject] — so nothing is ever approved or rejected from a
 * balloon click alone; this object itself never talks to the API.
 */
object ApprovalNotifier {
    private const val GROUP_ID = "Terraducktel approvals"

    /** Shows the balloon. [project] may be null (no project is open, or none is currently active)
     *  — the balloon itself still shows (the group is application-level); an action whose click
     *  needs a project just no-ops if none is available by the time it fires. */
    fun show(project: Project?, n: ApprovalNotice) {
        val summary = n.summary
        // RunGraph.summary is non-nullable (an all-zero default) — a null ApprovalNotice.summary
        // is the ONLY "unknown" signal, so show no counts at all rather than a misleading
        // "+0 to add, ~0 to change, -0 to destroy, ±0 to replace".
        val content = if (summary != null) {
            "+${summary.add} to add, ~${summary.change} to change, -${summary.destroy} to destroy, " +
                "±${summary.replace} to replace."
        } else {
            ""
        }
        val notification = NotificationGroupManager.getInstance()
            .getNotificationGroup(GROUP_ID)
            .createNotification(
                "TDT: ${n.workspaceName} ${n.run.command} awaits approval",
                content,
                NotificationType.INFORMATION,
            )
        notification.setImportant(true)
        notification.addAction(
            NotificationAction.createSimpleExpiring("Approve…") {
                project?.let { Approvals.approve(it, n.run) }
            },
        )
        notification.addAction(
            NotificationAction.createSimpleExpiring("Reject…") {
                project?.let { RejectAction.reject(it, n.run) }
            },
        )
        notification.addAction(
            NotificationAction.createSimpleExpiring("Open") {
                TdtSession.getInstance().uiUrl()?.let { ui -> BrowserUtil.browse("$ui/runs/${n.run.id}") }
            },
        )
        notification.notify(project)
    }
}
