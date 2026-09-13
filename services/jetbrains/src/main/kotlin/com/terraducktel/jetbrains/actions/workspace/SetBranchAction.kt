package com.terraducktel.jetbrains.actions.workspace

import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.application.ModalityState
import com.intellij.openapi.diagnostic.ControlFlowException
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.Messages
import com.intellij.openapi.ui.popup.JBPopupFactory
import com.terraducktel.jetbrains.actions.ActionUtil
import com.terraducktel.jetbrains.api.Branches
import com.terraducktel.jetbrains.api.Workspace
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.state.Store
import com.terraducktel.jetbrains.toolwindow.TdtDataKeys
import java.util.concurrent.CancellationException

/** Pins the selected workspace's tracked branch: lists the repo's branches in the background,
 *  offers a popup chooser (current branch marked, plus an "Other…" entry for a free-typed ref),
 *  then updates the workspace and refreshes. Port of VS Code's `terraducktel.setBranch`. */
class SetBranchAction : AnAction() {
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun update(e: AnActionEvent) {
        val ws = e.getData(TdtDataKeys.WORKSPACE)
        e.presentation.isEnabledAndVisible = ws != null && TdtSession.getInstance().canWrite()
    }

    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        val ws = e.getData(TdtDataKeys.WORKSPACE) ?: return

        ActionUtil.runBackground(project, "TDT: loading branches for ${ws.name}…") {
            val client = TdtSession.getInstance().requireClient()
            val branches = try {
                client.listBranches(ws.id)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (e is ControlFlowException) throw e
                Branches(source = "none", branches = emptyList())
            }
            // ModalityState.any() + a disposal condition: this must not queue up behind a modal
            // dialog, nor fire after the project is gone.
            ApplicationManager.getApplication().invokeLater(
                { pickBranch(project, ws, branches) },
                ModalityState.any(),
            ) { project.isDisposed }
        }
    }

    private fun pickBranch(project: Project, ws: Workspace, branches: Branches) {
        if (branches.branches.isEmpty()) {
            val ref = Messages.showInputDialog(project, "Tracked branch for ${ws.name}", "Set Tracked Branch", Messages.getQuestionIcon(), ws.repo_ref, null)
            applyBranch(project, ws, ref)
            return
        }
        val other = "Other…"
        val labelFor = { b: String -> if (b == ws.repo_ref) "$b (current)" else b }
        val byLabel = branches.branches.associateBy(labelFor)
        val options = branches.branches.map(labelFor) + other
        JBPopupFactory.getInstance()
            .createPopupChooserBuilder(options)
            .setTitle("Tracked branch for ${ws.name} (current: ${ws.repo_ref})")
            .setItemChosenCallback { picked ->
                if (picked == other) {
                    // Deferred rather than shown directly inside the popup's own callback: a modal
                    // dialog opened while the popup is still tearing itself down can misbehave
                    // (focus/parent-window issues) — invokeLater lets the popup finish closing
                    // first.
                    ApplicationManager.getApplication().invokeLater(
                        {
                            val ref = Messages.showInputDialog(project, "Branch / ref", "Set Tracked Branch", Messages.getQuestionIcon(), ws.repo_ref, null)
                            applyBranch(project, ws, ref)
                        },
                        ModalityState.any(),
                    ) { project.isDisposed }
                } else {
                    applyBranch(project, ws, byLabel[picked])
                }
            }
            .createPopup()
            .showCenteredInCurrentWindow(project)
    }

    private fun applyBranch(project: Project, ws: Workspace, ref: String?) {
        if (ref.isNullOrBlank() || ref == ws.repo_ref) return
        ActionUtil.runBackground(project, "TDT: updating tracked branch for ${ws.name}…") {
            TdtSession.getInstance().requireClient().updateWorkspace(ws.id, ref)
            Store.getInstance().refreshAndWait()
        }
    }
}
