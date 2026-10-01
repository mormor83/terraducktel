package com.terraducktel.jetbrains.toolwindow.nodes

import com.intellij.icons.AllIcons
import com.intellij.ide.projectView.PresentationData
import com.intellij.openapi.project.Project
import com.intellij.ui.SimpleTextAttributes
import com.terraducktel.jetbrains.api.BusinessUnit
import com.terraducktel.jetbrains.state.BuState
import com.terraducktel.jetbrains.state.Grouping
import com.terraducktel.jetbrains.state.Store
import com.terraducktel.jetbrains.toolwindow.RunsPanel

/**
 * Top-level row of both trees: one visible business unit. In the Workspaces tree ([View.WORKSPACES])
 * its children are that BU's cloud → region → folder → workspace grouping, computed from that BU's
 * workspaces alone; in the Runs tree ([View.RUNS]) they are that BU's runs. A BU whose last fetch
 * failed shows the error as its first child (any last-good data stays below it); an empty one shows
 * a single message. [autoExpand] is true when this is the only visible BU, which is expanded by
 * default (otherwise rows start collapsed; the tree remembers what the user opened via [id]).
 *
 * Reads the BU's current [BuState] from [Store] on every call rather than capturing it, so a
 * rebuild after a store tick always reflects the latest data.
 */
class BuNode(
    project: Project,
    parent: TdtNode?,
    val bu: BusinessUnit,
    val view: View,
    val autoExpand: Boolean,
) : TdtNode(project, parent) {

    enum class View { WORKSPACES, RUNS }

    override val id: String = "bu:${bu.slug}"

    private fun state(): BuState? = Store.getInstance().buState(bu.slug)

    override fun buildChildren(): List<TdtNode> {
        val state = state()
        if (state == null || (!state.loaded && state.error == null)) {
            return listOf(MessageNode(project, this, "Loading…"))
        }
        val children = mutableListOf<TdtNode>()
        state.error?.let { children += MessageNode(project, this, "Last refresh failed: $it", AllIcons.General.Warning) }
        when (view) {
            View.WORKSPACES -> {
                if (state.workspaces.isEmpty()) {
                    if (state.error == null) children += MessageNode(project, this, "No workspaces")
                } else {
                    children += Grouping.buildTree(state.workspaces).map { CloudGroupNode(project, this, bu.slug, it) }
                }
            }
            View.RUNS -> {
                if (state.runs.isEmpty()) {
                    if (state.error == null) children += MessageNode(project, this, "No recent runs")
                } else {
                    children += RunsPanel.sortRuns(state.runs).map { RunNode(project, this, bu.slug, it) }
                }
            }
        }
        return children
    }

    override fun update(presentation: PresentationData) {
        presentation.addText(bu.name, SimpleTextAttributes.REGULAR_ATTRIBUTES)
        presentation.addText("  ${NodeText.buDescription(bu, state(), view == View.WORKSPACES)}", SimpleTextAttributes.GRAYED_ATTRIBUTES)
        presentation.setIcon(AllIcons.Nodes.Module)
        presentation.tooltip = "${bu.name} (${bu.slug})"
    }
}
