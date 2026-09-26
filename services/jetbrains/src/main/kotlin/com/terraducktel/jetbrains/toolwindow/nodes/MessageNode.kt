package com.terraducktel.jetbrains.toolwindow.nodes

import com.intellij.icons.AllIcons
import com.intellij.ide.projectView.PresentationData
import com.intellij.openapi.project.Project
import javax.swing.Icon

/**
 * A leaf, informational row: "sign in", "no workspaces yet", a refresh-failed warning, a
 * loading placeholder, etc. Never reused across a rebuild (its [id] is instance-identity based,
 * unlike every other [TdtNode]), since two message rows can carry the same text but mean
 * different things depending on where in the tree they appear.
 */
class MessageNode(
    project: Project,
    parent: TdtNode?,
    private val text: String,
    private val icon: Icon = AllIcons.General.Information,
) : TdtNode(project, parent) {

    override val id: String = "message:${System.identityHashCode(this)}"

    override fun buildChildren(): List<TdtNode> = emptyList()

    override fun update(presentation: PresentationData) {
        presentation.presentableText = text
        presentation.setIcon(icon)
    }
}
