package com.terraducktel.jetbrains.toolwindow.nodes

import com.intellij.ide.projectView.PresentationData
import com.intellij.openapi.project.Project
import com.intellij.ui.SimpleTextAttributes
import com.terraducktel.jetbrains.state.CloudGroup
import com.terraducktel.jetbrains.toolwindow.TreeIcons

/** Top-level row of the Workspaces tree: one per cloud account / subscription / project (or, for
 *  workspaces that don't link to any of those, one per top-level path segment — see
 *  [com.terraducktel.jetbrains.state.Grouping]). Icon: the provider's brand glyph in the accent ink. */
class CloudGroupNode(
    project: Project,
    parent: TdtNode?,
    val group: CloudGroup,
) : TdtNode(project, parent) {

    override val id: String = "cloud:${group.cloud}:${group.key}"

    override fun buildChildren(): List<TdtNode> = group.regions.map { RegionNode(project, this, group, it) }

    override fun update(presentation: PresentationData) {
        presentation.addText(group.label, SimpleTextAttributes.REGULAR_ATTRIBUTES)
        presentation.addText("  ${NodeText.cloudDescription(group)}", SimpleTextAttributes.GRAYED_ATTRIBUTES)
        presentation.setIcon(TreeIcons.cloudIcon(group.cloud.name))
    }
}
