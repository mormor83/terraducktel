package com.terraducktel.jetbrains.actions.workspace

import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.terraducktel.jetbrains.output.RunActions
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.toolwindow.TdtDataKeys

/** Shared base for Plan/Apply/Destroy: visible+enabled iff a workspace is in the data context
 *  (the Workspaces tree's current selection) and a session is signed in. */
abstract class RunCommandActionBase(private val command: String) : AnAction() {
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun update(e: AnActionEvent) {
        val ws = e.getData(TdtDataKeys.WORKSPACE)
        e.presentation.isEnabledAndVisible = ws != null && TdtSession.getInstance().isSignedIn()
    }

    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        val ws = e.getData(TdtDataKeys.WORKSPACE) ?: return
        val bu = e.getData(TdtDataKeys.BU) ?: return
        RunActions.trigger(project, bu, ws, command)
    }
}
