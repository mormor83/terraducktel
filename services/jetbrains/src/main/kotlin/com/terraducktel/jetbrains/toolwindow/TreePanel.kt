package com.terraducktel.jetbrains.toolwindow

import com.intellij.icons.AllIcons
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
import com.terraducktel.jetbrains.toolwindow.nodes.MessageNode
import com.terraducktel.jetbrains.toolwindow.nodes.RunNode
import com.terraducktel.jetbrains.toolwindow.nodes.StepNode
import com.terraducktel.jetbrains.toolwindow.nodes.TdtNode
import com.terraducktel.jetbrains.toolwindow.nodes.WorkspaceNode
import org.jetbrains.concurrency.Promise
import java.util.concurrent.ConcurrentHashMap
import javax.swing.event.TreeExpansionEvent
import javax.swing.event.TreeExpansionListener
import javax.swing.tree.TreePath

/**
 * Shared plumbing for the Workspaces and Runs trees: a [SimpleTreeStructure] whose invisible root
 * is a [TdtNode] rebuilt from [Store]'s current snapshot on every redraw, wired to a
 * [StructureTreeModel] + [AsyncTreeModel] pair so the actual tree diffing happens off the EDT.
 * Subclasses supply the row content ([computeRootChildren]) and their context-menu group id
 * ([popupGroupId]); everything else (listener wiring, popup menu, tree actions, data-context,
 * reveal, the shared "nothing to show yet" head messages, and keeping [RunNode]'s step cache
 * fresh) lives here.
 *
 * Test seams: [signedInProvider] / [profileConfiguredProvider] let a test exercise the
 * "nothing to show yet" branch deterministically, without a real sign-in round trip against a
 * [com.terraducktel.jetbrains.testutil.StubServer]; [rebuild] synchronously computes the root's
 * children through the same [computeRootChildren] the real (async) tree uses, for direct
 * assertions; [expandedRunIds] / [refreshExpandedSteps] are `internal` so a test can simulate a
 * run being expanded without driving real Swing expansion events through the async tree.
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

    /** Run ids whose [RunNode] is currently expanded in THIS tree — populated by a
     *  [TreeExpansionListener] on [tree]. Read by [refreshExpandedSteps] to decide which
     *  non-terminal runs are worth refetching on a store tick; a collapsed (or never-expanded)
     *  run's steps are never proactively refreshed. */
    internal val expandedRunIds: MutableSet<String> = ConcurrentHashMap.newKeySet()

    init {
        Disposer.register(parentDisposable, this)

        rootNode = object : TdtNode(project, null) {
            override val id: String = "root"
            override val invalidate: (TdtNode, Boolean) -> Unit = { node, structural -> invalidateNode(node, structural) }

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

        tree.addTreeExpansionListener(object : TreeExpansionListener {
            override fun treeExpanded(event: TreeExpansionEvent) { runIdAt(event.path)?.let { expandedRunIds += it } }
            override fun treeCollapsed(event: TreeExpansionEvent) { runIdAt(event.path)?.let { expandedRunIds -= it } }
        })

        Store.getInstance().addListener(this) {
            val liveIds = Store.getInstance().runs.map { it.id }.toSet()
            RunNode.prune(liveIds)
            expandedRunIds.retainAll(liveIds)
            refreshExpandedSteps()
            scheduleInvalidate()
        }
        ApplicationManager.getApplication().messageBus.connect(this).subscribe(
            TdtSessionListener.TOPIC,
            object : TdtSessionListener {
                // The step cache belongs to one session (profile/BU/sign-in) — drop it all on
                // any of those changing, same as Store's own snapshot.
                override fun sessionChanged() {
                    RunNode.clearAll()
                    scheduleInvalidate()
                }
            },
        )
    }

    private fun runIdAt(path: TreePath): String? = (TreeUtil.getLastUserObject(TdtNode::class.java, path) as? RunNode)?.run?.id

    protected abstract fun popupGroupId(): String

    /** Builds the visible top-level rows. [root] is the (never-shown) root node — pass it as the
     *  `parent` of every returned node so the tree's parent chain (used by [TreeUtil]'s reveal /
     *  expand helpers) stays consistent. */
    protected abstract fun computeRootChildren(root: TdtNode): List<TdtNode>

    /** The `MessageNode`s both panels prepend before their real content: a warning when the last
     *  refresh failed, and another when the active profile has TLS verification off. Shared here
     *  so the two panels don't duplicate the same three lines. */
    protected fun headMessages(root: TdtNode): List<TdtNode> {
        val head = mutableListOf<TdtNode>()
        Store.getInstance().lastError?.let {
            head += MessageNode(project, root, "Last refresh failed: ${it.message}", AllIcons.General.Warning)
        }
        val profile = TdtSession.getInstance().profile
        if (profile?.insecureTls == true) {
            head += MessageNode(project, root, "Insecure TLS is on for profile ${profile.name}", AllIcons.General.Warning)
        }
        return head
    }

    /** The single row shown instead of any tree content while [signedInProvider] is false. */
    protected fun notReadyMessage(root: TdtNode): TdtNode =
        if (profileConfiguredProvider()) {
            MessageNode(project, root, "Sign in to Terraducktel", AllIcons.General.User)
        } else {
            MessageNode(project, root, "Add a profile under Settings → Tools → Terraducktel", AllIcons.General.User)
        }

    @Suppress("DEPRECATION") // Disposer.isDisposed(Disposable) has no non-deprecated replacement yet.
    private fun scheduleInvalidate() {
        ApplicationManager.getApplication().invokeLater {
            if (!Disposer.isDisposed(this)) structureModel.invalidateAsync()
        }
    }

    /** Every per-node `invalidate` call (the root's own, and every descendant's via [TdtNode.
     *  invalidate]) funnels through here so it gets the same off-EDT-safe, disposal-checked
     *  handling as [scheduleInvalidate] — in particular so a [RunNode]'s step-fetch completion,
     *  which can land well after the panel (and its `StructureTreeModel`) was disposed, never
     *  touches a disposed model. */
    @Suppress("DEPRECATION")
    private fun invalidateNode(node: TdtNode, structural: Boolean) {
        ApplicationManager.getApplication().invokeLater {
            if (!Disposer.isDisposed(this)) structureModel.invalidateAsync(node, structural)
        }
    }

    /** Refetches steps for every currently-[expandedRunIds] run, comparing each result against
     *  [RunNode]'s cache and redrawing only if at least one actually changed — called once per
     *  store tick (never from a node's own redraw), so this can never become the self-sustaining
     *  fetch loop the plain "refetch whenever asked to rebuild" approach was. Deliberately does
     *  NOT skip a run whose live status is already terminal: an expanded run that completes
     *  between one tick and the next still needs exactly one more refresh to pick up its final
     *  steps (and have its cache entry marked final) — [RunNode.refreshIfChanged] is what actually
     *  stops the refetching after that, once its own cache entry is final. `internal` so a test
     *  can call it directly after seeding [expandedRunIds], without driving real Swing expansion
     *  events through the async tree. */
    internal fun refreshExpandedSteps() {
        val ids = expandedRunIds.toSet()
        if (ids.isEmpty()) return
        val runsById = Store.getInstance().runs.associateBy { it.id }
        ApplicationManager.getApplication().executeOnPooledThread {
            var changed = false
            for (runId in ids) {
                val run = runsById[runId] ?: continue // pruned separately, from the same store tick
                if (RunNode.refreshIfChanged(runId, run.status)) changed = true
            }
            if (changed) scheduleInvalidate()
        }
    }

    /** Test seam: synchronously rebuilds the root's children (no async tree machinery), so a test
     *  can assert on the resulting [TdtNode]s directly. */
    internal fun rebuild(): List<TdtNode> = rootNode.buildChildren()

    /** No-op when a workspace with this id isn't currently in the tree (e.g. filtered by BU, or
     *  stale). Meaningless for [RunsPanel] (no `ws:` node ever appears there) — it simply never
     *  finds a match. Never descends into a [RunNode]/[StepNode]/[MessageNode] subtree (see
     *  [revealAction]) — a workspace is never nested inside a run, so there's nothing to find
     *  there, and descending would force every visited run's steps to be fetched. Called by
     *  [com.terraducktel.jetbrains.toolwindow.TdtToolWindowFactory.revealWorkspace]. Returns the
     *  underlying selection [Promise] (rather than discarding it) so a test can wait on it instead
     *  of racing the async tree; production callers are free to ignore it. */
    fun revealWorkspace(id: String): Promise<TreePath> {
        val targetId = "ws:$id"
        return TreeUtil.promiseSelect(
            tree,
            TreeVisitor { path -> revealAction(TreeUtil.getLastUserObject(TdtNode::class.java, path), targetId) },
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

    companion object {
        /** Pure decision function behind [revealWorkspace]'s [TreeVisitor] — split out so it can
         *  be unit tested directly against plain [TdtNode] instances, without needing to drive a
         *  real [TreePath] through the async tree machinery. [node] is null for a path segment
         *  the platform couldn't resolve to a [TdtNode] (shouldn't happen in this tree, but
         *  [TreeUtil.getLastUserObject] is nullable) — treated as a dead end. */
        internal fun revealAction(node: TdtNode?, targetId: String): TreeVisitor.Action = when {
            node == null -> TreeVisitor.Action.SKIP_CHILDREN
            node is WorkspaceNode -> if (node.id == targetId) TreeVisitor.Action.INTERRUPT else TreeVisitor.Action.SKIP_CHILDREN
            node is RunNode || node is StepNode || node is MessageNode -> TreeVisitor.Action.SKIP_CHILDREN
            else -> TreeVisitor.Action.CONTINUE // CloudGroupNode / RegionNode / FolderTreeNode / the hidden root
        }
    }
}
