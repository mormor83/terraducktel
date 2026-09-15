package com.terraducktel.jetbrains.toolwindow

import com.intellij.openapi.Disposable
import com.intellij.openapi.project.Project
import com.terraducktel.jetbrains.state.Grouping
import com.terraducktel.jetbrains.state.Store
import com.terraducktel.jetbrains.toolwindow.nodes.CloudGroupNode
import com.terraducktel.jetbrains.toolwindow.nodes.MessageNode
import com.terraducktel.jetbrains.toolwindow.nodes.TdtNode

/** The "Workspaces" tab: cloud → region → folder → workspace → run, built from [Store] via
 *  [Grouping]. See [TreePanel] for the shared tree plumbing. */
class WorkspacesPanel(project: Project, parentDisposable: Disposable) : TreePanel(project, parentDisposable) {

    override fun popupGroupId(): String = "Terraducktel.WorkspaceMenu"

    override fun computeRootChildren(root: TdtNode): List<TdtNode> {
        if (!signedInProvider()) return listOf(notReadyMessage(root))

        val head = headMessages(root)
        val content = Grouping.buildTree(Store.getInstance().workspaces).map { CloudGroupNode(project, root, it) }
        return if (content.isEmpty() && Store.getInstance().lastError == null) {
            head + MessageNode(project, root, "No workspaces in this business unit")
        } else {
            head + content
        }
    }
}
