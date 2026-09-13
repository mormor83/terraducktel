package com.terraducktel.jetbrains.actions.run

import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.ui.popup.JBPopupFactory
import com.terraducktel.jetbrains.output.RunActions
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.state.Store
import com.terraducktel.jetbrains.toolwindow.TdtDataKeys

/** Watches the selected run in the Runs tree's context menu, or — from the toolbar/Tools menu,
 *  where there is no selection — offers a popup chooser over every known run. Port of VS Code's
 *  `terraducktel.watchRun`. */
class WatchRunAction : AnAction("Watch Run") {
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun update(e: AnActionEvent) {
        e.presentation.isEnabledAndVisible = TdtSession.getInstance().isSignedIn()
    }

    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        val run = e.getData(TdtDataKeys.RUN)
        if (run != null) {
            RunActions.watch(project, run)
            return
        }
        val runs = Store.getInstance().runs
        val labels = runs.map { "${RunActions.wsName(it)} · ${it.command} — ${it.status} · ${it.id.take(8)}" }
        val byLabel = labels.zip(runs).toMap()
        JBPopupFactory.getInstance()
            .createPopupChooserBuilder(labels)
            .setTitle("Watch Run")
            .setItemChosenCallback { label -> byLabel[label]?.let { RunActions.watch(project, it) } }
            .createPopup()
            .showCenteredInCurrentWindow(project)
    }
}
