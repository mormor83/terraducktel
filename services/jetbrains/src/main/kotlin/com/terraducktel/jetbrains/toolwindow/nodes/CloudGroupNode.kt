package com.terraducktel.jetbrains.toolwindow.nodes

import com.intellij.icons.AllIcons
import com.intellij.ide.projectView.PresentationData
import com.intellij.openapi.project.Project
import com.intellij.ui.SimpleTextAttributes
import com.terraducktel.jetbrains.state.CloudGroup

/** Top-level row of the Workspaces tree: one per cloud account / subscription / project (or, for
 *  workspaces that don't link to any of those, one per top-level path segment — see
 *  [com.terraducktel.jetbrains.state.Grouping]). Every cloud uses the same icon; colour is not
 *  used to distinguish them (`AllIcons.Nodes.*` only, per the plugin's "no new colours" rule). */
class CloudGroupNode(
    project: Project,
    parent: TdtNode?,
    val group: CloudGroup,
) : TdtNode(project, parent) {

    override val id: String = "cloud:${group.cloud}:${group.key}"

    override fun buildChildren(): List<TdtNode> = group.regions.map { RegionNode(project, this, group, it) }

    override fun update(presentation: PresentationData) {
        presentation.addText(group.label, SimpleTextAttributes.REGULAR_ATTRIBUTES)
        presentation.addText(" (${group.count})", SimpleTextAttributes.GRAYED_ATTRIBUTES)
        presentation.setIcon(AllIcons.Nodes.PpWeb)
    }
}
