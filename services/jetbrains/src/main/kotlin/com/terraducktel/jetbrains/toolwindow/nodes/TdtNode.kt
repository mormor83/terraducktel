package com.terraducktel.jetbrains.toolwindow.nodes

import com.intellij.ide.projectView.PresentationData
import com.intellij.openapi.project.Project
import com.intellij.ui.treeStructure.SimpleNode

/**
 * Base of every row in the Terraducktel Workspaces / Runs trees. A thin [SimpleNode] with a
 * stable [id] used both as [getEqualityObjects] (so `StructureTreeModel.invalidateAsync()` keeps
 * a node's expansion/selection across a rebuild) and as [getName].
 *
 * [invalidate] is the live handle back to the owning [com.terraducktel.jetbrains.toolwindow.
 * TreePanel]'s `StructureTreeModel`, threaded down from the root node to every descendant through
 * [parent] so a deeply nested node (e.g. a lazily-loaded [RunNode]'s steps) can ask the tree to
 * redraw itself without holding a direct reference to the panel.
 */
abstract class TdtNode(project: Project, parent: TdtNode?) : SimpleNode(project, parent) {

    abstract val id: String

    internal open val invalidate: (TdtNode, Boolean) -> Unit = parent?.invalidate ?: { _, _ -> }

    /** Kotlin-friendly children hook — converted to the [SimpleNode] array [getChildren] wants. */
    internal abstract fun buildChildren(): List<TdtNode>

    final override fun getChildren(): Array<SimpleNode> {
        val list = buildChildren()
        return Array(list.size) { list[it] }
    }

    final override fun getEqualityObjects(): Array<Any> = arrayOf(id)
    override fun getName(): String = id
    abstract override fun update(presentation: PresentationData)
}
