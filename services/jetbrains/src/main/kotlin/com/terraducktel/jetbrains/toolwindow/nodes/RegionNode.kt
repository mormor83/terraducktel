package com.terraducktel.jetbrains.toolwindow.nodes

import com.intellij.icons.AllIcons
import com.intellij.ide.projectView.PresentationData
import com.intellij.openapi.project.Project
import com.intellij.ui.SimpleTextAttributes
import com.terraducktel.jetbrains.state.CloudGroup
import com.terraducktel.jetbrains.state.RegionGroup

/** Second-level row of the Workspaces tree: one per region within a [CloudGroupNode]. Children
 *  are the region's folder tree — subfolders first, then workspaces directly in this region's
 *  root folder — mirroring `services/vscode/src/views/workspacesTree.ts`'s `folderChildren`. */
class RegionNode(
    project: Project,
    parent: TdtNode?,
    val group: CloudGroup,
    val region: RegionGroup,
) : TdtNode(project, parent) {

    override val id: String = "region:${group.cloud}:${group.key}:${region.region}"

    override fun buildChildren(): List<TdtNode> =
        folderChildren(project, this, region.root, "${group.key}/${region.region}")

    override fun update(presentation: PresentationData) {
        presentation.addText(region.region, SimpleTextAttributes.REGULAR_ATTRIBUTES)
        presentation.addText(" (${region.count})", SimpleTextAttributes.GRAYED_ATTRIBUTES)
        presentation.setIcon(AllIcons.General.Locate)
    }
}
