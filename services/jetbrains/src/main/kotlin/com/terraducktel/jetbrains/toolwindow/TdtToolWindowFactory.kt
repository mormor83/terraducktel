package com.terraducktel.jetbrains.toolwindow

import com.intellij.openapi.Disposable
import com.intellij.openapi.actionSystem.ActionGroup
import com.intellij.openapi.actionSystem.ActionManager
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.project.DumbAware
import com.intellij.openapi.project.Project
import com.intellij.openapi.project.ProjectManager
import com.intellij.openapi.project.ProjectManagerListener
import com.intellij.openapi.ui.SimpleToolWindowPanel
import com.intellij.openapi.util.Disposer
import com.intellij.openapi.wm.ToolWindow
import com.intellij.openapi.wm.ToolWindowFactory
import com.intellij.openapi.wm.ToolWindowManager
import com.intellij.openapi.wm.ex.ToolWindowManagerListener
import com.intellij.ui.content.ContentFactory
import com.terraducktel.jetbrains.session.TdtSessionListener
import com.terraducktel.jetbrains.state.Store
import java.util.concurrent.atomic.AtomicBoolean
import javax.swing.JComponent

/** The "Terraducktel" tool window: a Workspaces tab and a Runs tab, each a [TreePanel] wrapped in
 *  its own toolbar-carrying [SimpleToolWindowPanel] (the shared `Terraducktel.Toolbar` action
 *  group, plus this task's [com.terraducktel.jetbrains.actions.RefreshAction]). */
class TdtToolWindowFactory : ToolWindowFactory, DumbAware {

    /** One named tab: [component] is what gets wrapped into a [com.intellij.ui.content.Content];
     *  [panel] is the concrete [TreePanel] it wraps, kept alongside (rather than requiring the
     *  caller to cast [component] back down) so production code and tests alike can reach
     *  panel-specific members (e.g. [RunsPanel.pendingApprovals]) directly. */
    internal data class ToolWindowContent(val name: String, val component: JComponent, val panel: TreePanel)

    override fun createToolWindowContent(project: Project, toolWindow: ToolWindow) {
        ensureProjectCloseListenerRegistered()

        val disposable = toolWindow.disposable
        val contents = buildContents(project, disposable)
        val contentManager = toolWindow.contentManager
        val createdContents = contents.map { c ->
            ContentFactory.getInstance().createContent(c.component, c.name, false).also { contentManager.addContent(it) }
        }

        val runsContent = createdContents[1]
        val runsPanel = contents[1].panel as RunsPanel

        fun updateRunsTitle() {
            ApplicationManager.getApplication().invokeLater {
                @Suppress("DEPRECATION") // Disposer.isDisposed(Disposable) has no non-deprecated replacement yet.
                if (Disposer.isDisposed(disposable)) return@invokeLater
                val pending = runsPanel.pendingApprovals()
                val title = if (pending > 0) "Runs · $pending" else "Runs"
                if (runsContent.displayName != title) runsContent.displayName = title
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
    internal fun buildContents(project: Project, disposable: Disposable): List<ToolWindowContent> {
        val workspaces = WorkspacesPanel(project, disposable)
        val runs = RunsPanel(project, disposable)
        return listOf(
            ToolWindowContent("Workspaces", wrapWithToolbar(workspaces), workspaces),
            ToolWindowContent("Runs", wrapWithToolbar(runs), runs),
        )
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

        /** [ToolWindowManagerListener] only fires per-project, so the LAST project to close never
         *  gets a chance to re-run [recomputeActive] via that path — its own tool window is gone
         *  by the time anything would ask. Registered once, application-wide, parented to
         *  [Store]'s own (application-level, plugin-lifetime) service instance rather than to any
         *  one project's disposable. */
        private val projectCloseListenerRegistered = AtomicBoolean(false)

        private fun ensureProjectCloseListenerRegistered() {
            if (!projectCloseListenerRegistered.compareAndSet(false, true)) return
            ApplicationManager.getApplication().messageBus.connect(Store.getInstance()).subscribe(
                ProjectManager.TOPIC,
                object : ProjectManagerListener {
                    override fun projectClosed(project: Project) { recomputeActive() }
                },
            )
        }
    }
}
