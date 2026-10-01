package com.terraducktel.jetbrains.actions.workspace

import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.ide.CopyPasteManager
import com.intellij.openapi.wm.WindowManager
import com.terraducktel.jetbrains.toolwindow.TdtDataKeys
import java.awt.datatransfer.StringSelection

/** Copies the selected run's or workspace's id to the clipboard. Port of VS Code's
 *  `terraducktel.copyId`. */
class CopyIdAction : AnAction() {
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun update(e: AnActionEvent) {
        val id = e.getData(TdtDataKeys.RUN)?.id ?: e.getData(TdtDataKeys.WORKSPACE)?.id
        e.presentation.isEnabledAndVisible = id != null
    }

    override fun actionPerformed(e: AnActionEvent) {
        val id = e.getData(TdtDataKeys.RUN)?.id ?: e.getData(TdtDataKeys.WORKSPACE)?.id ?: return
        CopyPasteManager.getInstance().setContents(StringSelection(id))
        e.project?.let { WindowManager.getInstance().getStatusBar(it)?.info = "Copied $id" }
    }
}
