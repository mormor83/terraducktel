package com.terraducktel.jetbrains.output

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.application.ModalityState
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.Messages
import com.terraducktel.jetbrains.actions.ActionUtil
import com.terraducktel.jetbrains.api.ApiError
import com.terraducktel.jetbrains.api.GraphSummary
import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.state.Store
import java.io.IOException

/**
 * The gated-approval modal shared by every caller that can approve a run: the Runs tree context
 * menu's `Terraducktel.Approve` action, the awaiting-approval balloon's "Approve…" action (wired
 * via [RunActions.approveHook]), and Plan 2's approval notifications. A port of `services/vscode/
 * src/commands/run.ts`'s `approve` command.
 */
object Approvals {

    /** Test seam: overridden by `ApprovalsTest` to swap in a fixed [Messages.YES]/[Messages.NO]/
     *  [Messages.CANCEL] answer, so the gate itself — nothing is POSTed to `/approve` without an
     *  explicit Approve — can be asserted deterministically against a stub server. Driving this
     *  through [Messages.setTestDialog] instead was considered and rejected: that hook was not
     *  verified to intercept a three-button [Messages.showDialog] in the 2026.1 platform, so this
     *  explicit seam is the deterministic choice instead of relying on unverified behaviour. */
    internal var confirm: (Project, Run, GraphSummary) -> Int = { project, run, summary -> defaultConfirm(project, run, summary) }

    internal val DIALOG_BUTTONS = arrayOf("Approve", "Show plan", "Cancel")

    /** `+2 to add, ~1 to change, -2 to destroy[, ±1 to replace]` — replace only when non-zero. */
    fun summaryText(summary: GraphSummary): String = buildList {
        add("+${summary.add} to add")
        add("~${summary.change} to change")
        add("-${summary.destroy} to destroy")
        if (summary.replace > 0) add("±${summary.replace} to replace")
    }.joinToString(", ")

    internal fun dialogTitle(run: Run, wsName: String): String = "Approve ${run.command} on $wsName?"

    internal fun dialogMessage(summary: GraphSummary): String =
        "${summaryText(summary)}\n\nNothing is applied until you click Approve."

    /** [Messages.showDialog]'s button index → the [confirm] seam's YES (Approve) / NO (Show plan) /
     *  CANCEL (Cancel, Esc or the window's close button, which report -1). */
    internal fun choiceFor(index: Int): Int = when (index) {
        0 -> Messages.YES
        1 -> Messages.NO
        else -> Messages.CANCEL
    }

    private fun defaultConfirm(project: Project, run: Run, summary: GraphSummary): Int =
        choiceFor(
            Messages.showDialog(
                project, dialogMessage(summary), dialogTitle(run, RunActions.wsName(run)),
                DIALOG_BUTTONS, 0, Messages.getQuestionIcon(),
            ),
        )

    /** Loads the plan's add/change/destroy/replace summary (best-effort — a failed fetch shows the
     *  dialog with all-zero counts rather than blocking the approval) then, on the EDT, asks
     *  Approve / Show plan / Cancel. Must be called on the EDT; the network calls run in
     *  background tasks.
     *
     *  [TdtSession.requireClient] is called OUTSIDE the summary try/catch: a signed-out session
     *  must fail fast (the same "not signed in" balloon every other action shows, via
     *  [ActionUtil.runBackground]'s own catch), not silently present an all-zero approve dialog as
     *  if the fetch had merely come back empty. Only [ApiError]/[IOException]/
     *  [IllegalStateException] — the set [ActionUtil.runBackground] itself treats as "expected,
     *  show a balloon" — are swallowed into a zero-count summary; a bare `catch (e: Exception)`
     *  here would also swallow `ProcessCanceledException` (and any other
     *  [com.intellij.openapi.progress.ProcessCanceledException]/`ControlFlowException`), silently
     *  breaking cooperative cancellation instead of letting it propagate. */
    fun approve(project: Project, run: Run) {
        val wsName = RunActions.wsName(run)
        ActionUtil.runBackground(project, "TDT: loading plan summary…") {
            val client = TdtSession.getInstance().requireClient()
            val summary = try {
                client.getGraph(run.id).summary
            } catch (e: ApiError) {
                GraphSummary()
            } catch (e: IOException) {
                GraphSummary()
            } catch (e: IllegalStateException) {
                GraphSummary()
            }
            // A disposed-project guard: a project can close while the summary fetch above is in
            // flight, and this must never pop a modal dialog (or touch Store/RunActions below) at
            // a dying project.
            ApplicationManager.getApplication().invokeLater(
                {
                    when (confirm(project, run, summary)) {
                        Messages.YES -> doApprove(project, run, wsName)
                        Messages.NO -> PlanDocument.open(project, run.id, wsName)
                        else -> Unit
                    }
                },
                ModalityState.nonModal(),
            ) { project.isDisposed }
        }
    }

    private fun doApprove(project: Project, run: Run, wsName: String) {
        ActionUtil.runBackground(project, "TDT: approving…") {
            TdtSession.getInstance().requireClient().approve(run.id)
            ActionUtil.notify(project, "TDT: approved $wsName ${run.command}.")
            Store.getInstance().refreshAndWait()
            ApplicationManager.getApplication().invokeLater(
                { RunActions.watch(project, run) },
                ModalityState.any(),
            ) { project.isDisposed }
        }
    }
}
