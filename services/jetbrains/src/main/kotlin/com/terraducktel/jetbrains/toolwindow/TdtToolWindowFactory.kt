package com.terraducktel.jetbrains.toolwindow

import com.intellij.execution.runners.ExecutionUtil
import com.intellij.openapi.Disposable
import com.intellij.openapi.actionSystem.ActionGroup
import com.intellij.openapi.actionSystem.ActionManager
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.project.DumbAware
import com.intellij.openapi.project.Project
import com.intellij.openapi.project.ProjectManager
import com.intellij.openapi.project.ProjectManagerListener
import com.intellij.openapi.util.Disposer
import com.intellij.openapi.wm.ToolWindow
import com.intellij.openapi.wm.ToolWindowFactory
import com.intellij.openapi.wm.ToolWindowManager
import com.intellij.openapi.wm.ex.ToolWindowManagerListener
import com.intellij.ui.content.ContentFactory
import com.terraducktel.jetbrains.TdtLog
import com.terraducktel.jetbrains.session.TdtSessionListener
import com.terraducktel.jetbrains.state.Store
import org.jetbrains.concurrency.Promise
import java.util.concurrent.atomic.AtomicBoolean
import javax.swing.tree.TreePath

/** The "Terraducktel" tool window: one [TdtStackedPanel] (Workspaces over Runs, each a collapsible
 *  section) under the shared `Terraducktel.Toolbar` action group. Run consoles live in the separate
 *  bottom "Terraducktel Run" window ([TdtRunToolWindowFactory]). */
class TdtToolWindowFactory : ToolWindowFactory, DumbAware {

    override fun createToolWindowContent(project: Project, toolWindow: ToolWindow) {
        ensureProjectCloseListenerRegistered()

        val disposable = toolWindow.disposable
        val panel = buildPanel(project, disposable)
        val group = ActionManager.getInstance().getAction("Terraducktel.Toolbar") as ActionGroup
        val toolbar = ActionManager.getInstance().createActionToolbar("TerraducktelToolWindow", group, true)
        toolbar.targetComponent = panel
        panel.toolbar = toolbar.component
        toolWindow.contentManager.addContent(ContentFactory.getInstance().createContent(panel, null, false))

        // The stripe icon carries the platform's "live" dot while any run waits at the gate.
        fun updateIcon() {
            ApplicationManager.getApplication().invokeLater {
                @Suppress("DEPRECATION") // Disposer.isDisposed(Disposable) has no non-deprecated replacement yet.
                if (Disposer.isDisposed(disposable)) return@invokeLater
                val awaiting = panel.runs.pendingApprovals() > 0
                toolWindow.setIcon(if (awaiting) ExecutionUtil.getLiveIndicator(TreeIcons.TOOL_WINDOW) else TreeIcons.TOOL_WINDOW)
            }
        }
        updateIcon()
        Store.getInstance().addListener(disposable) { updateIcon() }

        toolWindow.setAvailable(true)

        project.messageBus.connect(disposable).subscribe(
            ToolWindowManagerListener.TOPIC,
            object : ToolWindowManagerListener {
                override fun stateChanged(manager: ToolWindowManager) { recomputeActive() }
            },
        )
        recomputeActive()
    }

    /** Builds the stacked panel and keeps its Runs pill in sync with the store — without
     *  registering it with a [ToolWindow], so a headless test can exercise it directly. */
    internal fun buildPanel(project: Project, disposable: Disposable): TdtStackedPanel {
        val panel = TdtStackedPanel(project, disposable)
        fun updateBadge() {
            ApplicationManager.getApplication().invokeLater {
                @Suppress("DEPRECATION")
                if (Disposer.isDisposed(disposable)) return@invokeLater
                panel.runsSection.badge = panel.runs.pendingApprovals().takeIf { it > 0 }?.toString()
            }
        }
        updateBadge()
        Store.getInstance().addListener(disposable) { updateBadge() }
        ApplicationManager.getApplication().messageBus.connect(disposable).subscribe(
            TdtSessionListener.TOPIC,
            object : TdtSessionListener {
                override fun sessionChanged() { updateBadge() }
            },
        )
        return panel
    }

    companion object {
        const val TOOL_WINDOW_ID = "Terraducktel"

        /** Activates the tool window, expands its Workspaces section, and reveals [wsId] in it —
         *  the editor status bar's "Reveal in tool window" action and its "Reveal Workspace" menu
         *  counterpart both go through here. No-op if the tool window isn't registered (shouldn't
         *  happen) or [wsId] isn't currently in the Workspaces tree (see [TreePanel.revealWorkspace]) —
         *  each of those logs a warning explaining which step failed, rather than bailing out
         *  silently. */
        fun revealWorkspace(project: Project, wsId: String) {
            revealWorkspaceForTest(project, wsId)
        }

        /** Same as [revealWorkspace], but returns the underlying [Promise] (or null if it bailed
         *  out before reaching [TreePanel.revealWorkspace]) so a test can wait on the actual
         *  selection instead of racing [ToolWindow.activate]'s async callback. */
        internal fun revealWorkspaceForTest(project: Project, wsId: String): Promise<TreePath>? {
            val toolWindow = ToolWindowManager.getInstance(project).getToolWindow(TOOL_WINDOW_ID)
            if (toolWindow == null) {
                TdtLog.LOG.warn("Terraducktel: revealWorkspace($wsId) — the Terraducktel tool window isn't registered for this project")
                return null
            }
            var promise: Promise<TreePath>? = null
            toolWindow.activate {
                val panel = toolWindow.contentManager.contents.firstNotNullOfOrNull { it.component as? TdtStackedPanel }
                if (panel == null) {
                    TdtLog.LOG.warn("Terraducktel: revealWorkspace($wsId) — no tool window content is the stacked Workspaces/Runs panel")
                    return@activate
                }
                panel.workspacesSection.setCollapsed(false)
                promise = panel.workspaces.revealWorkspace(wsId)
            }
            return promise
        }

        /** Recomputed (never incrementally tracked) from every open project's tool window
         *  visibility — simpler and can't drift out of sync with reality. */
        private fun recomputeActive() {
            val anyVisible = ProjectManager.getInstance().openProjects.any {
                ToolWindowManager.getInstance(it).getToolWindow(TOOL_WINDOW_ID)?.isVisible == true
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
