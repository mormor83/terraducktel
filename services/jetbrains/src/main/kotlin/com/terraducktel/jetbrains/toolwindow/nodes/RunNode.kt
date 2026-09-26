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
import java.util.concurrent.atomic.AtomicInteger

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
        val epoch = sessionEpoch.get()
        ApplicationManager.getApplication().executeOnPooledThread {
            try {
                val client = TdtSession.getInstance().clientOrNull()
                if (client == null) return@executeOnPooledThread // no session (yet) — leave uncached, keep the placeholder
                val steps = client.getSteps(runId, includeOutput = false).sortedBy { it.position }
                if (epoch != sessionEpoch.get()) return@executeOnPooledThread // a session change landed mid-flight — drop this stale result
                stepsCache[runId] = StepsState.Loaded(steps, final = run.status in TERMINAL_RUN_STATUSES)
                invalidate(this, true)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (e is ControlFlowException) throw e
                if (epoch == sessionEpoch.get()) {
                    stepsCache[runId] = StepsState.Failed(e.message ?: "unknown error")
                    invalidate(this, true)
                }
            } finally {
                loading.remove(runId)
            }
        }
    }

    override fun update(presentation: PresentationData) {
        val wsName = Store.getInstance().workspace(run.workspace_id)?.name ?: run.workspace_id.take(8)
        presentation.addText(NodeText.runLabel(run, wsName, underWorkspace = parent is WorkspaceNode), SimpleTextAttributes.REGULAR_ATTRIBUTES)
        presentation.addText("  ${NodeText.runDescription(run)}", SimpleTextAttributes.GRAYED_ATTRIBUTES)
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

        /** Bounds [refreshIfChanged] to one in-flight tick-refetch per run id — two panels (or a
         *  fast tick landing before a slow one returns) must never issue a second concurrent
         *  request for the same run. Distinct from [loading] (which guards the first-expand fetch
         *  in [fetchStepsAsync]): the two can never actually race each other in practice, since
         *  [refreshIfChanged] refuses to run at all while there's no cache entry yet — but keeping
         *  them separate keeps each guard's single responsibility obvious. */
        private val refreshing = newKeySet<String>()

        /** Bumped by [clearAll]. Every fetch (initial or tick-driven) captures this before making
         *  its request and discards the result — no cache write, no `invalidate` — if it no longer
         *  matches once the request returns: a session change (profile/BU/sign-in) mid-flight must
         *  never let a response belonging to the OLD session land in the new one's cache. */
        private val sessionEpoch = AtomicInteger(0)

        /** Called by [TreePanel][com.terraducktel.jetbrains.toolwindow.TreePanel] on every store
         *  tick, for each currently-expanded run id — regardless of whether [liveStatus] (the
         *  run's CURRENT status per the store, which may differ from the stale [Run.status]
         *  captured on this or any other [RunNode] instance) is terminal, so a run that completes
         *  between expand and its next tick still gets one last refresh with its final steps and
         *  status. Performs one synchronous (caller is expected to be off the EDT already) fetch-
         *  and-compare: returns `true` only when the fetched result differs from what's cached
         *  (state type, `final`, or the steps themselves), having already updated the cache in
         *  that case — the caller redraws only then, never unconditionally. A [StepsState.Failed]
         *  entry is always eligible for a retry (treated like a non-final, empty [StepsState.
         *  Loaded] for comparison purposes); a [StepsState.Loaded] entry already marked `final` is
         *  never touched again — this is what actually stops the refetching once a run's final
         *  steps have been captured, since callers are not expected to (and, per above, no longer
         *  do) filter out terminal runs themselves. A run with no cache entry yet is not this
         *  function's job (that's the first-expand path in [fetchStepsAsync]); calling it for one
         *  is a harmless no-op. */
        internal fun refreshIfChanged(runId: String, liveStatus: String): Boolean {
            val previous = stepsCache[runId] ?: return false
            if (previous is StepsState.Loaded && previous.final) return false
            val epoch = sessionEpoch.get()
            val client = TdtSession.getInstance().clientOrNull() ?: return false
            if (!refreshing.add(runId)) return false
            try {
                val steps = try {
                    client.getSteps(runId, includeOutput = false).sortedBy { it.position }
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    if (e is ControlFlowException) throw e
                    return false // transient error during a background refresh — leave the last-good cache alone
                }
                if (epoch != sessionEpoch.get()) return false // a session change landed mid-flight — drop this stale result
                val final = liveStatus in TERMINAL_RUN_STATUSES
                val unchanged = previous is StepsState.Loaded && previous.final == final && previous.steps == steps
                if (unchanged) return false
                stepsCache[runId] = StepsState.Loaded(steps, final)
                return true
            } finally {
                refreshing.remove(runId)
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
         *  session's cached output). Bumping [sessionEpoch] additionally guards against a fetch
         *  that was already in flight when the session changed landing its (now stale) result
         *  afterwards. */
        internal fun clearAll() {
            stepsCache.clear()
            loading.clear()
            refreshing.clear()
            sessionEpoch.incrementAndGet()
        }
    }
}
