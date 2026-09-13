package com.terraducktel.jetbrains.actions.auth

import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.ui.popup.JBPopupFactory
import com.terraducktel.jetbrains.actions.ActionUtil
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.settings.TdtSettings

class SwitchProfileAction : AnAction() {
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun update(e: AnActionEvent) {
        e.presentation.isEnabled = TdtSettings.getInstance().state.profiles.isNotEmpty()
    }

    override fun actionPerformed(e: AnActionEvent) {
        val session = TdtSession.getInstance()
        val active = session.profile?.name
        val names = TdtSettings.getInstance().state.profiles.map { it.name }
        val labelToName = names.associateBy { if (it == active) "$it ✓" else it }
        val project = e.project

        val popup = JBPopupFactory.getInstance()
            .createPopupChooserBuilder(labelToName.keys.toList())
            .setTitle("Switch Profile")
            .setItemChosenCallback { label ->
                val name = labelToName[label] ?: return@setItemChosenCallback
                ActionUtil.runBackground(project, "Terraducktel: switching profile…") {
                    session.setActiveProfile(name)
                }
            }
            .createPopup()
        if (project != null) popup.showCenteredInCurrentWindow(project) else popup.showInFocusCenter()
    }
}
