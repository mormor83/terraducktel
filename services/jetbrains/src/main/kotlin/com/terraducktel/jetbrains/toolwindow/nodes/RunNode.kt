package com.terraducktel.jetbrains.toolwindow.nodes

import com.intellij.ide.projectView.PresentationData
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.project.Project
import com.intellij.ui.SimpleTextAttributes
import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.api.RunStep
import com.terraducktel.jetbrains.api.TERMINAL_RUN_STATUSES
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.state.Store
import com.terraducktel.jetbrains.toolwindow.TreeIcons
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.ConcurrentHashMap.newKeySet

/** A row in both trees: a top-level entry in the Runs tree, and a child of a [WorkspaceNode] in
 *  the Workspaces tree. Its children are [StepNode]s, fetched lazily and off the EDT the first
 *  time this node is expanded — never eagerly, so opening either tree never fires N step
 *  requests for N runs. [stepsCache] is keyed by run id and shared by every [RunNode] instance
 *  (a fresh one is built on every tree rebuild): once a run reaches a [TERMINAL_RUN_STATUSES]
 *  status its steps can never change again, so the cache entry survives rebuilds; a still-running
 *  run is re-fetched on every expand. */
class RunNode(
    project: Project,
    parent: TdtNode?,
    val run: Run,
) : TdtNode(project, parent) {

    override val id: String = "run:${run.id}"

    override fun buildChildren(): List<TdtNode> {
        val cached = stepsCache[run.id]
        if (cached != null) return stepsToNodes(cached)
        fetchStepsAsync()
        return listOf(MessageNode(project, this, "loading…"))
    }

    private fun stepsToNodes(steps: List<RunStep>): List<TdtNode> =
        if (steps.isEmpty()) listOf(MessageNode(project, this, "No steps yet"))
        else steps.sortedBy { it.position }.map { StepNode(project, this, it) }

    private fun fetchStepsAsync() {
        val runId = run.id
        if (!loading.add(runId)) return // already in flight — the eventual invalidate() will pick up its result
        ApplicationManager.getApplication().executeOnPooledThread {
            try {
                val client = TdtSession.getInstance().clientOrNull()
                val steps = client?.getSteps(runId, includeOutput = false) ?: emptyList()
                if (run.status in TERMINAL_RUN_STATUSES) stepsCache[runId] = steps
                invalidate(this, true)
            } catch (_: Exception) {
                // Leave uncached: the next expand (invalidateAsync from a later Store change, or a
                // manual refresh) retries rather than sticking on a permanent error.
            } finally {
                loading.remove(runId)
            }
        }
    }

    override fun update(presentation: PresentationData) {
        val wsName = Store.getInstance().workspace(run.workspace_id)?.name ?: run.workspace_id.take(8)
        presentation.addText("$wsName · ${run.command}", SimpleTextAttributes.REGULAR_ATTRIBUTES)
        val secondary = listOfNotNull(run.status, run.id.take(8), run.created_at).joinToString(" · ")
        presentation.addText(" · $secondary", SimpleTextAttributes.GRAYED_ATTRIBUTES)
        presentation.setIcon(TreeIcons.runStatusIcon(run.status))
        presentation.tooltip = "${run.command} · ${run.status}\n${run.id}\ncreated ${run.created_at ?: "-"}"
    }

    companion object {
        private val stepsCache = ConcurrentHashMap<String, List<RunStep>>()
        private val loading = newKeySet<String>()
    }
}
