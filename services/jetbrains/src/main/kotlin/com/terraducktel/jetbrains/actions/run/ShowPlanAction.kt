package com.terraducktel.jetbrains.actions.run

import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.ui.popup.JBPopupFactory
import com.terraducktel.jetbrains.output.PlanDocument
import com.terraducktel.jetbrains.output.RunActions
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.state.Store
import com.terraducktel.jetbrains.toolwindow.TdtDataKeys

/** Opens the selected run's plan output in a read-only, decorated scratch document — the Runs
 *  tree context menu's counterpart to the awaiting-approval balloon's "Show plan" action. With no
 *  run in the data context (Tools menu / toolbar), offers a popup chooser over every known run,
 *  same as [WatchRunAction]. Port of VS Code's `terraducktel.showPlan`. */
class ShowPlanAction : AnAction("Show Plan") {
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun update(e: AnActionEvent) {
        e.presentation.isEnabledAndVisible = TdtSession.getInstance().isSignedIn()
    }

    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        val run = e.getData(TdtDataKeys.RUN)
        if (run != null) {
            PlanDocument.open(project, run.id, RunActions.wsName(run))
            return
        }
        val runs = Store.getInstance().runs
        val labels = runs.map { "${RunActions.wsName(it)} · ${it.command} — ${it.status} · ${it.id.take(8)}" }
        val byLabel = labels.zip(runs).toMap()
        JBPopupFactory.getInstance()
            .createPopupChooserBuilder(labels)
            .setTitle("Show Plan")
            .setItemChosenCallback { label -> byLabel[label]?.let { PlanDocument.open(project, it.id, RunActions.wsName(it)) } }
            .createPopup()
            .showCenteredInCurrentWindow(project)
    }
}
