package com.terraducktel.jetbrains.toolwindow

import com.intellij.openapi.Disposable
import com.intellij.openapi.actionSystem.ActionGroup
import com.intellij.openapi.actionSystem.ActionManager
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.project.DumbAware
import com.intellij.openapi.project.Project
import com.intellij.openapi.project.ProjectManager
import com.intellij.openapi.ui.SimpleToolWindowPanel
import com.intellij.openapi.wm.ToolWindow
import com.intellij.openapi.wm.ToolWindowFactory
import com.intellij.openapi.wm.ToolWindowManager
import com.intellij.openapi.wm.ex.ToolWindowManagerListener
import com.intellij.ui.content.ContentFactory
import com.terraducktel.jetbrains.session.TdtSessionListener
import com.terraducktel.jetbrains.state.Store
import javax.swing.JComponent

/** The "Terraducktel" tool window: a Workspaces tab and a Runs tab, each a [TreePanel] wrapped in
 *  its own toolbar-carrying [SimpleToolWindowPanel] (the shared `Terraducktel.Toolbar` action
 *  group, plus this task's [com.terraducktel.jetbrains.actions.RefreshAction]). */
class TdtToolWindowFactory : ToolWindowFactory, DumbAware {

    override fun createToolWindowContent(project: Project, toolWindow: ToolWindow) {
        val disposable = toolWindow.disposable
        val contents = buildContents(project, disposable)
        val contentManager = toolWindow.contentManager
        val createdContents = contents.map { (name, component) ->
            ContentFactory.getInstance().createContent(component, name, false).also { contentManager.addContent(it) }
        }

        // contents[1] is "Runs" — see buildContents. Its RunsPanel is nested one level down,
        // inside the toolbar-carrying wrapper buildContents returned.
        val runsContent = createdContents[1]
        val runsPanel = (contents[1].second as SimpleToolWindowPanel).content as RunsPanel

        fun updateRunsTitle() {
            ApplicationManager.getApplication().invokeLater {
                val pending = runsPanel.pendingApprovals()
                runsContent.displayName = if (pending > 0) "Runs · $pending" else "Runs"
            }
        }
        updateRunsTitle()
        Store.getInstance().addListener(disposable) { updateRunsTitle() }
        ApplicationManager.getApplication().messageBus.connect(disposable).subscribe(
            TdtSessionListener.TOPIC,
            object : TdtSessionListener {
                override fun sessionChanged() { updateRunsTitle() }
            },
        )

        toolWindow.setAvailable(true)

        project.messageBus.connect(disposable).subscribe(
            ToolWindowManagerListener.TOPIC,
            object : ToolWindowManagerListener {
                override fun stateChanged(manager: ToolWindowManager) { recomputeActive() }
            },
        )
        recomputeActive()
    }

    /** Builds the two tab contents (panel + toolbar wrapper), without registering them with a
     *  [ToolWindow] — split out so a headless test can exercise it without needing to register a
     *  real tool window. */
    internal fun buildContents(project: Project, disposable: Disposable): List<Pair<String, JComponent>> {
        val workspaces = WorkspacesPanel(project, disposable)
        val runs = RunsPanel(project, disposable)
        return listOf("Workspaces" to wrapWithToolbar(workspaces), "Runs" to wrapWithToolbar(runs))
    }

    private fun wrapWithToolbar(inner: TreePanel): JComponent {
        val outer = SimpleToolWindowPanel(true, true)
        val group = ActionManager.getInstance().getAction("Terraducktel.Toolbar") as ActionGroup
        val toolbar = ActionManager.getInstance().createActionToolbar("TerraducktelToolWindow", group, true)
        toolbar.targetComponent = inner
        outer.toolbar = toolbar.component
        outer.setContent(inner)
        return outer
    }

    companion object {
        /** Recomputed (never incrementally tracked) from every open project's tool window
         *  visibility — simpler and can't drift out of sync with reality. */
        private fun recomputeActive() {
            val anyVisible = ProjectManager.getInstance().openProjects.any {
                ToolWindowManager.getInstance(it).getToolWindow("Terraducktel")?.isVisible == true
            }
            Store.getInstance().setActive(anyVisible)
        }
    }
}
