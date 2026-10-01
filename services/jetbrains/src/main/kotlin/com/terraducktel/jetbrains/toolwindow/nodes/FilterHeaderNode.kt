package com.terraducktel.jetbrains.toolwindow.nodes

import com.intellij.icons.AllIcons
import com.intellij.ide.projectView.PresentationData
import com.intellij.openapi.project.Project

/** First root row while some business units are hidden: "Showing X of Y business units — Filter…".
 *  Activating it (double-click / Enter, wired in [com.terraducktel.jetbrains.toolwindow.TreePanel])
 *  opens the business-unit filter. */
class FilterHeaderNode(
    project: Project,
    parent: TdtNode?,
    val shown: Int,
    val total: Int,
) : TdtNode(project, parent) {

    override val id: String = "filter-header"

    override fun buildChildren(): List<TdtNode> = emptyList()

    override fun update(presentation: PresentationData) {
        presentation.presentableText = NodeText.filterHeader(shown, total)
        presentation.setIcon(AllIcons.General.Filter)
    }
}
