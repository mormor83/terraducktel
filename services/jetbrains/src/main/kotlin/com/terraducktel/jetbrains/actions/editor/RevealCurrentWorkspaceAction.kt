package com.terraducktel.jetbrains.actions.editor

import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.terraducktel.jetbrains.editor.EditorStatus
import com.terraducktel.jetbrains.toolwindow.TdtToolWindowFactory

/** Reveals the active file's mapped workspace in the Workspaces section. Port of VS Code's
 *  `terraducktel.revealCurrentWorkspace`. */
class RevealCurrentWorkspaceAction : AnAction() {
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun update(e: AnActionEvent) {
        val project = e.project
        e.presentation.isEnabledAndVisible = project != null && EditorStatus.getInstance(project).isMapped
    }

    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        EditorStatus.getInstance(project).resolveCurrent { TdtToolWindowFactory.revealWorkspace(project, it.ws.id) }
    }
}
