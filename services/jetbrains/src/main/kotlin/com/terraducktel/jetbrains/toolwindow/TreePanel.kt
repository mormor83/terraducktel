package com.terraducktel.jetbrains.toolwindow

import com.intellij.ide.projectView.PresentationData
import com.intellij.openapi.Disposable
import com.intellij.openapi.actionSystem.DataSink
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.SimpleToolWindowPanel
import com.intellij.openapi.util.Disposer
import com.intellij.ui.PopupHandler
import com.intellij.ui.components.JBScrollPane
import com.intellij.ui.tree.AsyncTreeModel
import com.intellij.ui.tree.StructureTreeModel
import com.intellij.ui.tree.TreeVisitor
import com.intellij.ui.treeStructure.SimpleTreeStructure
import com.intellij.ui.treeStructure.Tree
import com.intellij.util.ui.tree.TreeUtil
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.session.TdtSessionListener
import com.terraducktel.jetbrains.state.Store
import com.terraducktel.jetbrains.toolwindow.nodes.RunNode
import com.terraducktel.jetbrains.toolwindow.nodes.TdtNode
import com.terraducktel.jetbrains.toolwindow.nodes.WorkspaceNode

/**
 * Shared plumbing for the Workspaces and Runs trees: a [SimpleTreeStructure] whose invisible root
 * is a [TdtNode] rebuilt from [Store]'s current snapshot on every redraw, wired to a
 * [StructureTreeModel] + [AsyncTreeModel] pair so the actual tree diffing happens off the EDT.
 * Subclasses supply the row content ([computeRootChildren]) and their context-menu group id
 * ([popupGroupId]); everything else (listener wiring, popup menu, tree actions, data-context,
 * reveal) lives here.
 *
 * Test seams: [signedInProvider] / [profileConfiguredProvider] let a test exercise the
 * "nothing to show yet" branch deterministically, without a real sign-in round trip against a
 * [com.terraducktel.jetbrains.testutil.StubServer]; [rebuild] synchronously computes the root's
 * children through the same [computeRootChildren] the real (async) tree uses, for direct
 * assertions.
 */
abstract class TreePanel(
    protected val project: Project,
    parentDisposable: Disposable,
) : SimpleToolWindowPanel(true, true), Disposable {

    internal var signedInProvider: () -> Boolean = { TdtSession.getInstance().isSignedIn() }
    internal var profileConfiguredProvider: () -> Boolean = { TdtSession.getInstance().profile != null }

    protected val structureModel: StructureTreeModel<*>
    protected val tree: Tree
    private val rootNode: TdtNode

    init {
        Disposer.register(parentDisposable, this)

        rootNode = object : TdtNode(project, null) {
            override val id: String = "root"
            override val invalidate: (TdtNode, Boolean) -> Unit = { node, structural ->
                structureModel.invalidateAsync(node, structural)
            }

            override fun buildChildren(): List<TdtNode> = computeRootChildren(this)
            override fun update(presentation: PresentationData) {}
        }
        val structure = object : SimpleTreeStructure() {
            override fun getRootElement(): Any = rootNode
        }
        structureModel = StructureTreeModel(structure, null, this)
        val asyncModel = AsyncTreeModel(structureModel, this)
        tree = Tree(asyncModel)
        tree.isRootVisible = false
        TreeUtil.installActions(tree)
        PopupHandler.installPopupMenu(tree, popupGroupId(), "TerraducktelTree")
        setContent(JBScrollPane(tree))

        Store.getInstance().addListener(this) { scheduleInvalidate() }
        ApplicationManager.getApplication().messageBus.connect(this).subscribe(
            TdtSessionListener.TOPIC,
            object : TdtSessionListener {
                override fun sessionChanged() { scheduleInvalidate() }
            },
        )
    }

    protected abstract fun popupGroupId(): String

    /** Builds the visible top-level rows. [root] is the (never-shown) root node — pass it as the
     *  `parent` of every returned node so the tree's parent chain (used by [TreeUtil]'s reveal /
     *  expand helpers) stays consistent. */
    protected abstract fun computeRootChildren(root: TdtNode): List<TdtNode>

    @Suppress("DEPRECATION") // Disposer.isDisposed(Disposable) has no non-deprecated replacement yet.
    private fun scheduleInvalidate() {
        ApplicationManager.getApplication().invokeLater {
            if (!Disposer.isDisposed(this)) structureModel.invalidateAsync()
        }
    }

    /** Test seam: synchronously rebuilds the root's children (no async tree machinery), so a test
     *  can assert on the resulting [TdtNode]s directly. */
    internal fun rebuild(): List<TdtNode> = rootNode.buildChildren()

    /** No-op when a workspace with this id isn't currently in the tree (e.g. filtered by BU, or
     *  stale). Meaningless for [RunsPanel] (no `ws:` node ever appears there) — it simply never
     *  finds a match. */
    fun revealWorkspace(id: String) {
        TreeUtil.promiseSelect(
            tree,
            TreeVisitor { path ->
                val node = TreeUtil.getLastUserObject(TdtNode::class.java, path)
                if (node != null && node.id == "ws:$id") TreeVisitor.Action.INTERRUPT else TreeVisitor.Action.CONTINUE
            },
        )
    }

    override fun uiDataSnapshot(sink: DataSink) {
        super.uiDataSnapshot(sink)
        when (val selected = TreeUtil.getLastUserObject(TdtNode::class.java, tree.selectionPath)) {
            is WorkspaceNode -> sink[TdtDataKeys.WORKSPACE] = selected.ws
            is RunNode -> sink[TdtDataKeys.RUN] = selected.run
            else -> {}
        }
    }

    override fun dispose() {}
}
