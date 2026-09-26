package com.terraducktel.jetbrains.actions.workspace

import com.intellij.ide.BrowserUtil
import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.toolwindow.TdtDataKeys

/** Opens the selected workspace's (`<uiUrl>/`) or run's (`<uiUrl>/runs/<id>`) page in the system
 *  browser. Port of VS Code's `terraducktel.openInBrowser`. */
class OpenInBrowserAction : AnAction() {
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun update(e: AnActionEvent) {
        val ws = e.getData(TdtDataKeys.WORKSPACE)
        val run = e.getData(TdtDataKeys.RUN)
        e.presentation.isEnabledAndVisible = (ws != null || run != null) && TdtSession.getInstance().uiUrl() != null
    }

    override fun actionPerformed(e: AnActionEvent) {
        val ui = TdtSession.getInstance().uiUrl() ?: return
        val run = e.getData(TdtDataKeys.RUN)
        val ws = e.getData(TdtDataKeys.WORKSPACE)
        val url = when {
            run != null -> "$ui/runs/${run.id}"
            ws != null -> "$ui/"
            else -> return
        }
        BrowserUtil.browse(url)
    }
}
