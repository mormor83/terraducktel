package com.terraducktel.jetbrains.toolwindow.nodes

import com.intellij.ide.projectView.PresentationData
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.diagnostic.ControlFlowException
import com.intellij.openapi.project.Project
import com.intellij.ui.SimpleTextAttributes
import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.api.RunStep
import com.terraducktel.jetbrains.api.TERMINAL_RUN_STATUSES
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.state.Store
import com.terraducktel.jetbrains.toolwindow.TreeIcons
import java.util.concurrent.CancellationException
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.ConcurrentHashMap.newKeySet

/** A row in both trees: a top-level entry in the Runs tree, and a child of a [WorkspaceNode] in
 *  the Workspaces tree. Its children are [StepNode]s, fetched lazily and off the EDT the first
 *  time this node is expanded — never eagerly, so opening either tree never fires N step
 *  requests for N runs.
 *
 *  [stepsCache] holds the *last known* result for every run this session has ever expanded,
 *  terminal or not, keyed by run id and shared by every [RunNode] instance (a fresh one is built
 *  on every tree rebuild). Critically, [buildChildren] only ever fetches when there is NO cache
 *  entry at all — a cached [StepsState.Loaded] (even a non-final one, i.e. a still-running run)
 *  is returned as-is. Without this, a still-running run's steps would never satisfy the terminal
 *  check the old code used to decide whether to cache, so every rebuild (including the one this
 *  very fetch's own [invalidate] call triggers) would see an empty cache and fetch again —
 *  hammering the API at network-round-trip rate for as long as the run stayed open. Refreshing a
 *  cached-but-non-final entry is instead [TreePanel][com.terraducktel.jetbrains.toolwindow.
 *  TreePanel]'s job, driven off actual store ticks rather than off this node's own redraws — see
 *  [refreshIfChanged]. */
class RunNode(
    project: Project,
    parent: TdtNode?,
    val run: Run,
) : TdtNode(project, parent) {

    override val id: String = "run:${run.id}"

    override fun buildChildren(): List<TdtNode> = when (val state = stepsCache[run.id]) {
        is StepsState.Loaded -> stepsToNodes(state.steps)
        is StepsState.Failed -> listOf(MessageNode(project, this, "Steps unavailable: ${state.message}"))
        null -> {
            fetchStepsAsync()
            listOf(MessageNode(project, this, "loading…"))
        }
    }

    private fun stepsToNodes(steps: List<RunStep>): List<TdtNode> =
        if (steps.isEmpty()) listOf(MessageNode(project, this, "No steps yet")) else steps.map { StepNode(project, this, it) }

    /** First-expand fetch only — populates [stepsCache] so every later [buildChildren] (including
     *  the one this fetch's own completion triggers) is a cache hit. */
    private fun fetchStepsAsync() {
        val runId = run.id
        if (!loading.add(runId)) return // already in flight — its completion will invalidate this node
        ApplicationManager.getApplication().executeOnPooledThread {
            try {
                val client = TdtSession.getInstance().clientOrNull()
                if (client == null) return@executeOnPooledThread // no session (yet) — leave uncached, keep the placeholder
                val steps = client.getSteps(runId, includeOutput = false).sortedBy { it.position }
                stepsCache[runId] = StepsState.Loaded(steps, final = run.status in TERMINAL_RUN_STATUSES)
                invalidate(this, true)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (e is ControlFlowException) throw e
                stepsCache[runId] = StepsState.Failed(e.message ?: "unknown error")
                invalidate(this, true)
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
        internal sealed class StepsState {
            data class Loaded(val steps: List<RunStep>, val final: Boolean) : StepsState()
            data class Failed(val message: String) : StepsState()
        }

        private val stepsCache = ConcurrentHashMap<String, StepsState>()
        private val loading = newKeySet<String>()

        /** Called by [TreePanel][com.terraducktel.jetbrains.toolwindow.TreePanel] on every store
         *  tick, for each currently-expanded run id whose live status is not yet terminal.
         *  Performs one synchronous (caller is expected to be off the EDT already) fetch-and-
         *  compare: returns `true` only when the fetched steps differ from what's cached, having
         *  already updated the cache in that case — the caller redraws only then, never
         *  unconditionally. A run with no cache entry yet is not this function's job (that's the
         *  first-expand path in [fetchStepsAsync]); calling it for one is a harmless no-op. */
        internal fun refreshIfChanged(runId: String): Boolean {
            val client = TdtSession.getInstance().clientOrNull() ?: return false
            val previous = stepsCache[runId] as? StepsState.Loaded ?: return false
            if (previous.final) return false // belt-and-braces: a final entry is never touched again, even if a caller's own terminal-status check is ever wrong or stale
            return try {
                val steps = client.getSteps(runId, includeOutput = false).sortedBy { it.position }
                if (steps == previous.steps) return false
                stepsCache[runId] = StepsState.Loaded(steps, final = false)
                true
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (e is ControlFlowException) throw e
                false // transient error during a background refresh — leave the last-good cache alone
            }
        }

        /** Drops cache entries for runs no longer present in the store's current snapshot — port
         *  of `runsTree.ts`'s `prune()`. */
        internal fun prune(liveRunIds: Set<String>) {
            stepsCache.keys.retainAll(liveRunIds)
        }

        /** The whole step cache belongs to one session (profile/BU/sign-in): a session change
         *  invalidates every cached result, terminal or not (a different BU can reuse a run id
         *  from a different backend in theory, and a fresh sign-in should never show another
         *  session's cached output). */
        internal fun clearAll() {
            stepsCache.clear()
            loading.clear()
        }
    }
}
