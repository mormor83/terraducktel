package com.terraducktel.jetbrains.actions

import com.intellij.icons.AllIcons
import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.state.Store

/** Manual "refresh now" for the Terraducktel tool window toolbar — bypasses the poll interval. */
class RefreshAction : AnAction("Refresh", "Refresh Terraducktel workspaces and runs", AllIcons.Actions.Refresh) {
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun update(e: AnActionEvent) {
        e.presentation.isEnabled = TdtSession.getInstance().isSignedIn()
    }

    override fun actionPerformed(e: AnActionEvent) {
        Store.getInstance().refresh()
    }
}
