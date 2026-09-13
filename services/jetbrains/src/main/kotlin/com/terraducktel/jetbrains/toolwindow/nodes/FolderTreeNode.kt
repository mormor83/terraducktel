package com.terraducktel.jetbrains.toolwindow.nodes

import com.intellij.icons.AllIcons
import com.intellij.ide.projectView.PresentationData
import com.intellij.openapi.project.Project
import com.intellij.ui.SimpleTextAttributes
import com.terraducktel.jetbrains.state.FolderNode

/** A synthetic path segment between a [RegionNode] and its [WorkspaceNode] leaves — named
 *  `FolderTreeNode` (not `FolderNode`) to avoid clashing with [com.terraducktel.jetbrains.state.
 *  FolderNode], the plain-data node [com.terraducktel.jetbrains.state.Grouping] builds. [path] is
 *  the folder's full slash-joined path from the region root; it (not [FolderNode.name] alone) is
 *  what makes [id] unique across the whole tree. */
class FolderTreeNode(
    project: Project,
    parent: TdtNode?,
    val folder: FolderNode,
    val path: String,
) : TdtNode(project, parent) {

    override val id: String = "folder:$path"

    override fun buildChildren(): List<TdtNode> = folderChildren(project, this, folder, path)

    override fun update(presentation: PresentationData) {
        presentation.addText(folder.name, SimpleTextAttributes.REGULAR_ATTRIBUTES)
        presentation.setIcon(AllIcons.Nodes.Folder)
    }
}

/** Shared by [RegionNode] and [FolderTreeNode]: subfolders (sorted, via [FolderNode.folders]'s
 *  `SortedMap` backing) first, then the workspaces directly in this folder. */
internal fun folderChildren(project: Project, parent: TdtNode, folder: FolderNode, path: String): List<TdtNode> {
    val folders = folder.folders.values.map { FolderTreeNode(project, parent, it, "$path/${it.name}") }
    val leaves = folder.workspaces.map { (ws, leaf) -> WorkspaceNode(project, parent, ws, leaf) }
    return folders + leaves
}
