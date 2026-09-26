package com.terraducktel.jetbrains.toolwindow.nodes

import com.intellij.ide.projectView.PresentationData
import com.intellij.openapi.project.Project
import com.intellij.ui.SimpleTextAttributes
import com.terraducktel.jetbrains.api.Workspace
import com.terraducktel.jetbrains.state.Store
import com.terraducktel.jetbrains.toolwindow.TreeIcons

/** A leaf of the Workspaces tree. [leaf] is the folder-relative display name computed by
 *  [com.terraducktel.jetbrains.state.Grouping] and is the row's label (same as the VS Code tree);
 *  the full [Workspace.name] is in the tooltip. Children are the workspace's own runs, newest first
 *  (as returned by [Store.runsFor]). */
class WorkspaceNode(
    project: Project,
    parent: TdtNode?,
    val ws: Workspace,
    val leaf: String,
) : TdtNode(project, parent) {

    override val id: String = "ws:${ws.id}"

    override fun buildChildren(): List<TdtNode> =
        Store.getInstance().runsFor(ws.id).map { RunNode(project, this, it) }

    override fun update(presentation: PresentationData) {
        val lastRun = Store.getInstance().runsFor(ws.id).firstOrNull()
        presentation.addText(leaf, SimpleTextAttributes.REGULAR_ATTRIBUTES)
        presentation.addText("  ${NodeText.workspaceDescription(ws, lastRun?.status)}", SimpleTextAttributes.GRAYED_ATTRIBUTES)
        presentation.setIcon(TreeIcons.runStatusIcon(lastRun?.status))
        presentation.tooltip = buildTooltip()
    }

    private fun buildTooltip(): String = buildString {
        append(ws.name).append('\n')
        append("id: ").append(ws.id).append('\n')
        append("path: ").append(ws.tf_working_dir).append('\n')
        ws.repo_url?.let { append("repo: ").append(it).append('\n') }
        append("environment: ").append(ws.environment)
        if (ws.tags.isNotEmpty()) {
            append("\ntags: ").append(ws.tags.entries.joinToString(", ") { (k, v) -> "$k=$v" })
        }
    }
}
