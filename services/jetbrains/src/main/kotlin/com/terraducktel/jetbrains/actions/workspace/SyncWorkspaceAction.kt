package com.terraducktel.jetbrains.actions.workspace

import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.application.ModalityState
import com.intellij.openapi.project.Project
import com.terraducktel.jetbrains.actions.ActionUtil
import com.terraducktel.jetbrains.api.Workspace
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.state.Store
import com.terraducktel.jetbrains.toolwindow.TdtDataKeys

/** Requests a repo-sync for the selected workspace. Port of VS Code's `terraducktel.syncWorkspace`. */
class SyncWorkspaceAction : AnAction() {
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun update(e: AnActionEvent) {
        val ws = e.getData(TdtDataKeys.WORKSPACE)
        e.presentation.isEnabledAndVisible = ws != null && TdtSession.getInstance().isSignedIn()
    }

    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        val ws = e.getData(TdtDataKeys.WORKSPACE) ?: return
        val bu = e.getData(TdtDataKeys.BU) ?: return
        sync(project, bu, ws)
    }

    companion object {
        /** Requests the repo-sync through the client of [bu] — the workspace's own business unit. */
        internal fun sync(project: Project, bu: String, ws: Workspace) {
            ActionUtil.runBackground(project, "TDT: syncing ${ws.name}…") {
                TdtSession.getInstance().requireClient(bu).syncWorkspace(ws.id)
                Store.getInstance().refreshAndWait()
                // ModalityState.any() + a disposal condition: must not queue up behind a modal dialog,
                // nor fire after the project is gone.
                ApplicationManager.getApplication().invokeLater(
                    { ActionUtil.notify(project, "TDT: sync requested for ${ws.name}.") },
                    ModalityState.any(),
                ) { project.isDisposed }
            }
        }
    }
}
