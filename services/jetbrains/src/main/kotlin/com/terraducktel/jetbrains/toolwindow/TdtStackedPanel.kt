package com.terraducktel.jetbrains.toolwindow

import com.intellij.icons.AllIcons
import com.intellij.openapi.Disposable
import com.intellij.openapi.actionSystem.ActionManager
import com.intellij.openapi.actionSystem.DataSink
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.project.DumbAwareAction
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.SimpleToolWindowPanel
import com.intellij.ui.OnePixelSplitter
import com.intellij.util.ui.JBUI
import java.awt.event.ComponentAdapter
import java.awt.event.ComponentEvent
import java.awt.event.FocusAdapter
import java.awt.event.FocusEvent

/**
 * Body of the "Terraducktel" tool window: a Workspaces section stacked over a Runs section (the
 * VS Code sidebar's layout), split 3:2 by a [OnePixelSplitter]. A collapsed section shrinks to its
 * header and the other one takes the free space; each section's state is remembered per project.
 */
class TdtStackedPanel(project: Project, parentDisposable: Disposable) : SimpleToolWindowPanel(true, true) {

    val workspaces = WorkspacesPanel(project, parentDisposable)
    val runs = RunsPanel(project, parentDisposable)

    private val splitter = OnePixelSplitter(true, DEFAULT_PROPORTION)
    /** The user's last proportion while both sections were open — restored when one reopens. */
    private var openProportion = DEFAULT_PROPORTION
    /** True while a collapsed section has the splitter pinned to header height. */
    private var pinned = false

    private val refresh = ActionManager.getInstance().getAction("Terraducktel.Refresh")

    val workspacesSection = CollapsibleSection(
        project, "Workspaces", workspaces, WORKSPACES_COLLAPSED_KEY,
        listOfNotNull(refresh, object : DumbAwareAction("Collapse All", null, AllIcons.Actions.Collapseall) {
            override fun actionPerformed(e: AnActionEvent) { workspaces.collapseAll() }
        }),
    ) { layoutSections() }

    val runsSection = CollapsibleSection(project, "Runs", runs, RUNS_COLLAPSED_KEY, listOfNotNull(refresh)) { layoutSections() }

    /** The tree whose selection toolbar actions (e.g. Watch Run…) act on: the last one focused. */
    private var lastFocused: TreePanel = workspaces

    init {
        for (panel in listOf(workspaces, runs)) {
            panel.treeComponent.addFocusListener(object : FocusAdapter() {
                override fun focusGained(e: FocusEvent) { lastFocused = panel }
            })
        }
        splitter.firstComponent = workspacesSection
        splitter.secondComponent = runsSection
        setContent(splitter)
        splitter.addComponentListener(object : ComponentAdapter() {
            override fun componentResized(e: ComponentEvent) { layoutSections() }
        })
        layoutSections()
    }

    override fun uiDataSnapshot(sink: DataSink) {
        super.uiDataSnapshot(sink)
        lastFocused.uiDataSnapshot(sink)
    }

    /** Pins the splitter so a collapsed section only keeps its header's height. */
    private fun layoutSections() {
        val wsOpen = !workspacesSection.isCollapsed
        val runsOpen = !runsSection.isCollapsed
        if (wsOpen && runsOpen) {
            if (pinned) splitter.proportion = openProportion
            pinned = false
            splitter.setResizeEnabled(true)
            return
        }
        if (!pinned) openProportion = splitter.proportion
        pinned = true
        splitter.setResizeEnabled(false)
        val total = splitter.height.takeIf { it > 0 } ?: return
        val header = JBUI.scale(CollapsibleSection.HEADER_HEIGHT).toFloat()
        splitter.proportion = when {
            !wsOpen -> (header / total).coerceIn(0f, 1f)
            else -> (1f - header / total).coerceIn(0f, 1f) // only Runs collapsed
        }
    }

    companion object {
        const val WORKSPACES_COLLAPSED_KEY = "terraducktel.section.workspaces.collapsed"
        const val RUNS_COLLAPSED_KEY = "terraducktel.section.runs.collapsed"
        private const val DEFAULT_PROPORTION = 0.6f
    }
}
