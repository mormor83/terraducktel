package com.terraducktel.jetbrains.actions.editor

import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.terraducktel.jetbrains.editor.EditorStatus

/** Shows the same actions popup the status-bar item's click shows — the keyboard/menu equivalent
 *  of clicking it. Always enabled (mirrors the widget itself: unmapped still shows a one-item
 *  "Open in browser" popup rather than disappearing). */
class CurrentFileActionsAction : AnAction() {
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun update(e: AnActionEvent) {
        e.presentation.isEnabledAndVisible = e.project != null
    }

    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        EditorStatus.getInstance(project).actionsPopup().showInBestPositionFor(e.dataContext)
    }
}
