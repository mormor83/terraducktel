package com.terraducktel.jetbrains.toolwindow.nodes

import com.intellij.ide.projectView.PresentationData
import com.intellij.openapi.project.Project
import com.intellij.ui.treeStructure.SimpleNode

/**
 * Base of every row in the Terraducktel Workspaces / Runs trees. A thin [SimpleNode] with a
 * stable [id] used as [getEqualityObjects] so `StructureTreeModel.invalidateAsync()` keeps a
 * node's expansion/selection across a rebuild. [getName] is deliberately left to
 * [SimpleNode]'s/`PresentableNodeDescriptor`'s own implementation — it derives the visible label
 * from the rendered [PresentationData] (falling back to a template name), which is what speed
 * search and similar "find by name" platform features expect; [id] is an internal identity, not a
 * display string.
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
    abstract override fun update(presentation: PresentationData)
}
