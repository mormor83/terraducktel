package com.terraducktel.jetbrains.actions.auth

import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.ui.popup.JBPopupFactory
import com.terraducktel.jetbrains.actions.ActionUtil
import com.terraducktel.jetbrains.session.TdtSession

class SwitchBusinessUnitAction : AnAction() {
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun update(e: AnActionEvent) {
        val session = TdtSession.getInstance()
        e.presentation.isEnabled = session.isSignedIn() && session.tokens?.kind() != "api_key"
    }

    override fun actionPerformed(e: AnActionEvent) {
        val session = TdtSession.getInstance()
        val project = e.project
        ActionUtil.runBackground(project, "Terraducktel: loading business units…") {
            val slugs = session.requireClient().listBusinessUnits().map { it.slug }
            val superadmin = session.tokens?.claims()?.is_superadmin == true
            val options = if (superadmin) slugs + "all" else slugs
            ApplicationManager.getApplication().invokeLater {
                val popup = JBPopupFactory.getInstance()
                    .createPopupChooserBuilder(options)
                    .setTitle("Switch Business Unit")
                    .setItemChosenCallback { slug ->
                        ActionUtil.runBackground(project, "Terraducktel: switching business unit…") {
                            session.setBu(slug)
                        }
                    }
                    .createPopup()
                if (project != null) popup.showCenteredInCurrentWindow(project) else popup.showInFocusCenter()
            }
        }
    }
}
