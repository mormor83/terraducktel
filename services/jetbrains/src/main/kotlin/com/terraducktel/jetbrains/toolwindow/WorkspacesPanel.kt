package com.terraducktel.jetbrains.toolwindow

import com.intellij.openapi.Disposable
import com.intellij.openapi.project.Project
import com.terraducktel.jetbrains.state.Store
import com.terraducktel.jetbrains.toolwindow.nodes.BuNode
import com.terraducktel.jetbrains.toolwindow.nodes.MessageNode
import com.terraducktel.jetbrains.toolwindow.nodes.TdtNode

/** The "Workspaces" section: one row per visible business unit, each holding the cloud → region →
 *  folder → workspace → run tree of that BU alone (built from [Store] via
 *  [com.terraducktel.jetbrains.state.Grouping]). See [TreePanel] for the shared tree plumbing. */
class WorkspacesPanel(project: Project, parentDisposable: Disposable) : TreePanel(project, parentDisposable) {

    override fun popupGroupId(): String = "Terraducktel.WorkspaceMenu"

    override fun computeRootChildren(root: TdtNode): List<TdtNode> = buRootChildren(root, BuNode.View.WORKSPACES)
}
