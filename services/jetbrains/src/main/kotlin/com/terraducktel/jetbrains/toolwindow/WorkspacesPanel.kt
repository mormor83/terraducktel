package com.terraducktel.jetbrains.toolwindow

import com.intellij.icons.AllIcons
import com.intellij.openapi.Disposable
import com.intellij.openapi.project.Project
import com.terraducktel.jetbrains.session.TdtSession
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

        val head = mutableListOf<TdtNode>()
        Store.getInstance().lastError?.let {
            head += MessageNode(project, root, "Last refresh failed: ${it.message}", AllIcons.General.Warning)
        }
        val profile = TdtSession.getInstance().profile
        if (profile?.insecureTls == true) {
            head += MessageNode(project, root, "Insecure TLS is on for profile ${profile.name}", AllIcons.General.Warning)
        }

        val content = Grouping.buildTree(Store.getInstance().workspaces).map { CloudGroupNode(project, root, it) }
        if (content.isEmpty() && Store.getInstance().lastError == null) {
            head += MessageNode(project, root, "No workspaces in this business unit")
        }
        return head + content
    }

    private fun notReadyMessage(root: TdtNode): TdtNode =
        if (profileConfiguredProvider()) {
            MessageNode(project, root, "Sign in to Terraducktel", AllIcons.General.User)
        } else {
            MessageNode(project, root, "Add a profile under Settings → Tools → Terraducktel", AllIcons.General.User)
        }
}
