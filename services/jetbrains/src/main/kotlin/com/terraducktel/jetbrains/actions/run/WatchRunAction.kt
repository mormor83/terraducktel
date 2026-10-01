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
class WatchRunAction : AnAction() {
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun update(e: AnActionEvent) {
        e.presentation.isEnabledAndVisible = TdtSession.getInstance().isSignedIn()
    }

    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        val run = e.getData(TdtDataKeys.RUN)
        val bu = e.getData(TdtDataKeys.BU)
        if (run != null && bu != null) {
            RunActions.watch(project, bu, run)
            return
        }
        // No run selected (Tools menu / toolbar): choose among the runs of every visible BU.
        val runs = Store.getInstance().allRuns()
        val labels = RunActions.chooserLabels(runs)
        val byLabel = labels.zip(runs).toMap()
        JBPopupFactory.getInstance()
            .createPopupChooserBuilder(labels)
            .setTitle("Watch Run")
            .setItemChosenCallback { label -> byLabel[label]?.let { RunActions.watch(project, it.bu.slug, it.run) } }
            .createPopup()
            .showCenteredInCurrentWindow(project)
    }
}
