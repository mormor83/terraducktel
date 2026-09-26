package com.terraducktel.jetbrains.actions.editor

import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.terraducktel.jetbrains.editor.EditorStatus
import com.terraducktel.jetbrains.session.TdtSession

/** Plans the workspace the active file maps to — the editor-menu/Tools-menu counterpart of the
 *  status-bar item's "Plan this leaf". Port of VS Code's `terraducktel.planCurrentFile`. */
class PlanCurrentFileAction : AnAction() {
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun update(e: AnActionEvent) {
        val project = e.project
        val mapped = project != null && EditorStatus.getInstance(project).current != null
        e.presentation.isEnabledAndVisible = mapped && TdtSession.getInstance().canWrite()
    }

    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        EditorStatus.getInstance(project).planCurrent()
    }
}
