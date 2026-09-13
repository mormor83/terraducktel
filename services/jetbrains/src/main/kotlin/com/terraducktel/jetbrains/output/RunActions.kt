package com.terraducktel.jetbrains.output

import com.intellij.notification.NotificationType
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.MessageDialogBuilder
import com.intellij.openapi.ui.Messages
import com.terraducktel.jetbrains.actions.ActionUtil
import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.api.TriggerRunBody
import com.terraducktel.jetbrains.api.Workspace
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.state.Store

/**
 * Entry points shared by every caller that triggers a run or watches one land: the Workspaces/
 * Runs tree context menus, the toolbar/Tools-menu "Watch Run" action, and (per Plan 2) the editor
 * status bar's "Plan this leaf". A port of the relevant parts of `services/vscode/src/commands/
 * workspace.ts` (`runCommandFor`) and `commands/run.ts` (`watch`/`announceAwaiting`).
 */
object RunActions {

    /** Advisory dedupe hook for Plan 2's `ApprovalWatcher.markSeen` — told about a run landing in
     *  `awaiting_approval` BEFORE this window's own toast goes up, so a background approval poll
     *  never announces the same run a second time. A failure here must never suppress the toast
     *  itself — see [announceAwaiting]. */
    var onAwaitingHook: ((Run) -> Unit)? = null

    /** Wired by [com.terraducktel.jetbrains.session.TdtSessionStarter] to [PlanDocument.open] /
     *  [Approvals.approve] — the awaiting-approval balloon's "Show plan"/"Approve…" actions. */
    var showPlanHook: ((Project, Run) -> Unit)? = null
    var approveHook: ((Project, Run) -> Unit)? = null

    fun wsName(run: Run): String = Store.getInstance().workspace(run.workspace_id)?.name ?: run.workspace_id.take(8)

    /** Opens/reveals the console tab for [run] and starts (or resumes) following it. When it
     *  lands, refreshes the store and — for `awaiting_approval`/`failed` — surfaces a balloon.
     *  Must be called on the EDT. */
    fun watch(project: Project, run: Run) {
        RunConsoles.getInstance(project).watch(run.id, wsName(run)) { landed ->
            Store.getInstance().refresh()
            when (landed.status) {
                "awaiting_approval" -> announceAwaiting(project, landed)
                "failed" -> ActionUtil.notify(project, "TDT: ${wsName(landed)} ${landed.command} failed — see the run output.", NotificationType.ERROR)
            }
        }
    }

    /** Must be called on the EDT (it shows a notification with actions). */
    fun announceAwaiting(project: Project, run: Run) {
        // Best-effort dedupe: if marking it seen fails we would rather show the balloon twice than
        // not at all, so a failure here never stops the balloon below.
        try {
            onAwaitingHook?.invoke(run)
        } catch (_: Exception) {
            // dedupe is advisory
        }
        ActionUtil.notify(
            project,
            "TDT: ${wsName(run)} ${run.command} is awaiting approval.",
            NotificationType.INFORMATION,
            "Show plan" to { showPlanHook?.invoke(project, run) },
            "Approve…" to { approveHook?.invoke(project, run) },
        )
    }

    /** Pure decision behind [trigger]: whether the workspace needs pinning to [branch] first, and
     *  the run-trigger body to send. Split out so the pin / no-pin / `branch == ws.repo_ref` cases
     *  can be unit tested without any platform machinery. */
    internal data class TriggerPlan(val pin: String?, val body: TriggerRunBody)

    internal fun triggerPlanFor(ws: Workspace, command: String, branch: String?): TriggerPlan {
        val pin = if (branch != null && branch != ws.repo_ref) branch else null
        return TriggerPlan(pin, TriggerRunBody(command))
    }

    /** The pin already landed server-side by the time the trigger call itself fails; say so, or a
     *  failed apply/destroy trigger reads as if nothing happened at all when in fact the
     *  workspace's tracked branch just changed. */
    internal fun pinFailedMessage(branch: String, command: String, cause: Throwable): String =
        "pinned to $branch, but $command failed: ${cause.message}"

    /**
     * Port of VS Code `runCommandFor`: the Apply confirmation and the Destroy type-the-name guard
     * apply to EVERY caller (context menu, Tools menu, and Plan 2's status-bar "Plan this leaf").
     * When [branch] differs from `ws.repo_ref` the workspace is pinned to it first. Must be called
     * on the EDT; the network calls run in a background task.
     */
    fun trigger(project: Project, ws: Workspace, command: String, branch: String? = null) {
        if (command == "apply") {
            val ok = MessageDialogBuilder.yesNo("Apply ${ws.name}?", "The plan will pause for approval before anything changes.")
                .yesText("Start apply")
                .asWarning()
                .ask(project)
            if (!ok) return
        }
        if (command == "destroy") {
            val typed = Messages.showInputDialog(
                project,
                "Type the workspace name to confirm DESTROY: ${ws.name}",
                "Destroy ${ws.name}",
                Messages.getWarningIcon(),
            )
            if (typed != ws.name) return
        }

        val plan = triggerPlanFor(ws, command, branch)
        ActionUtil.runBackground(project, "TDT: $command ${ws.name}") {
            val client = TdtSession.getInstance().requireClient()
            if (plan.pin != null) client.updateWorkspace(ws.id, plan.pin)
            val run = try {
                client.triggerRun(ws.id, plan.body)
            } catch (e: Exception) {
                if (plan.pin != null) throw IllegalStateException(pinFailedMessage(plan.pin, command, e)) else throw e
            }
            Store.getInstance().refreshAndWait()
            ApplicationManager.getApplication().invokeLater {
                ActionUtil.notify(project, "TDT: $command started on ${ws.name} (${run.id.take(8)}).")
                watch(project, run)
            }
        }
    }
}
