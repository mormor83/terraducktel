package com.terraducktel.jetbrains.actions.auth

import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.terraducktel.jetbrains.actions.ActionUtil
import com.terraducktel.jetbrains.session.TdtSession

class SignOutAction : AnAction() {
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun update(e: AnActionEvent) {
        e.presentation.isEnabled = TdtSession.getInstance().isSignedIn()
    }

    override fun actionPerformed(e: AnActionEvent) {
        val session = TdtSession.getInstance()
        ActionUtil.runBackground(e.project, "Terraducktel: signing out…") {
            session.signOut()
        }
    }
}
