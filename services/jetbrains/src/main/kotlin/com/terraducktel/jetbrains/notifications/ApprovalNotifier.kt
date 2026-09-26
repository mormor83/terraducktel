package com.terraducktel.jetbrains.notifications

import com.intellij.ide.BrowserUtil
import com.intellij.ide.impl.ProjectUtil
import com.intellij.notification.NotificationAction
import com.intellij.notification.NotificationGroupManager
import com.intellij.notification.NotificationType
import com.intellij.openapi.project.Project
import com.intellij.openapi.project.ProjectManager
import com.intellij.openapi.util.text.StringUtil
import com.terraducktel.jetbrains.actions.ActionUtil
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
    const val TITLE = "Terraducktel approvals"

    /** Balloon body: the sentence, a line break, then the verbose summary. HTML so the balloon wraps
     *  it (never `nowrap`). [ApprovalNotice.summary] null is the ONLY "unknown" signal —
     *  [com.terraducktel.jetbrains.api.RunGraph.summary] is non-nullable (an all-zero default) — so
     *  then show no counts at all rather than a misleading "+0 to add, ~0 to change, -0 to destroy". */
    internal fun body(n: ApprovalNotice): String {
        val sentence = "TDT: ${StringUtil.escapeXmlEntities(n.workspaceName)} ${StringUtil.escapeXmlEntities(n.run.command)} awaits approval"
        val counts = n.summary?.let { "<br>${Approvals.summaryText(it)}" } ?: ""
        return "<html>$sentence$counts</html>"
    }

    /** The project to act against for a balloon action click — resolved lazily AT CLICK TIME, not
     *  when the balloon was shown: the balloon (an app-level notification) can easily outlive the
     *  project it was posted for (welcome screen, or every project closed while it was sitting
     *  there), and the original `project?.let { … }` pattern just silently did nothing in that case
     *  even if a project was open again by the time the user actually clicked. Falls back to any
     *  open project when there's no "active" one (e.g. focus is on the welcome screen). */
    private fun projectForAction(): Project? =
        ProjectUtil.getActiveProject() ?: ProjectManager.getInstance().openProjects.firstOrNull()

    /** Shows the balloon. [project] may be null (no project is open, or none is currently active)
     *  — the balloon itself still shows (the group is application-level); Approve…/Reject… resolve
     *  their own project lazily at click time (see [projectForAction]) rather than closing over
     *  [project], so they still work if [project] was null (or has since closed) but some project
     *  is open by the time the user clicks. */
    fun show(project: Project?, n: ApprovalNotice) {
        val notification = NotificationGroupManager.getInstance()
            .getNotificationGroup(GROUP_ID)
            .createNotification(TITLE, body(n), NotificationType.INFORMATION)
        notification.setImportant(true)
        notification.addAction(
            NotificationAction.createSimpleExpiring("Approve…") {
                val p = projectForAction()
                if (p != null) Approvals.approve(p, n.run) else noProjectOpenError()
            },
        )
        notification.addAction(
            NotificationAction.createSimpleExpiring("Reject…") {
                val p = projectForAction()
                if (p != null) RejectAction.reject(p, n.run) else noProjectOpenError()
            },
        )
        notification.addAction(
            NotificationAction.createSimpleExpiring("Open") {
                TdtSession.getInstance().uiUrl()?.let { ui -> BrowserUtil.browse("$ui/runs/${n.run.id}") }
            },
        )
        notification.notify(project)
    }

    /** Shown in place of a silent no-op when Approve…/Reject… is clicked with no project open at
     *  all (welcome screen, every project closed). `Open` needs no project and is unaffected. */
    private fun noProjectOpenError() {
        ActionUtil.notify(null, "Terraducktel: open a project to approve or reject this run.", NotificationType.ERROR)
    }
}
