package com.terraducktel.jetbrains.notifications

import com.intellij.openapi.Disposable
import com.terraducktel.jetbrains.api.GraphSummary
import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.api.TdtClient
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.util.concurrent.CompletableFuture
import java.util.concurrent.ExecutionException

/**
 * Persisted "seen" store: run id -> epoch millis it was first observed awaiting approval. Backed
 * by a real IntelliJ persistence mechanism in Task 4's wiring; a plain in-memory map in tests
 * (including a variant whose [set] throws, to exercise a failed persist).
 */
interface SeenStore {
    fun get(): Map<String, Long>
    fun set(v: Map<String, Long>)
}

/** One run awaiting approval the watcher decided to surface. [summary] is null when the graph
 *  fetch for this run failed — the notice still goes out, just without counts. */
data class ApprovalNotice(val run: Run, val workspaceName: String, val summary: GraphSummary?)

/**
 * Polls for runs awaiting approval and raises each one once (per 24h, across reloads/restarts). A
 * line-for-line port of `services/vscode/src/notifications/approvals.ts`'s `ApprovalWatcher`. Pure:
 * no IntelliJ UI here — the real `notify`/`workspaceName` plumbing (balloons, tool window lookups)
 * is Task 4's job. Every public method (other than [start]/[stop]/[dispose]) is blocking; call off
 * the EDT.
 */
class ApprovalWatcher(
    private val client: () -> TdtClient?,
    private val workspaceName: (String) -> String,
    private val notify: (ApprovalNotice) -> Unit,
    private val seen: SeenStore,
    private val now: () -> Long = System::currentTimeMillis,
    private val trace: ((String) -> Unit)? = null,
    private val ttlMs: Long = 24 * 3_600_000,
    private val scope: CoroutineScope,
) : Disposable {

    /** False until the current backlog has been recorded as seen. While false the watcher NEVER
     *  notifies: it records whatever it fetched and flips to true. That covers both a [prime] that
     *  failed (network down at wake-up) and a [poll] whose response lands before a concurrent
     *  [prime]'s — either way the first thing a fresh session does is swallow the backlog. */
    @Volatile private var primed = false

    // Single-flight guard for poll(): a second call while one is in flight joins the same
    // CompletableFuture rather than issuing a second request, mirroring the TS `inflight` promise.
    private val pollLock = Any()
    private var inflight: CompletableFuture<Unit>? = null

    /** Guards every seen-map read-modify-write (recordSilently, markSeen, doPoll's own update):
     *  markSeen() is called from the run-console tail thread while the poll loop may be inside
     *  doPoll() on its own thread, and an unsynchronised read -> mutate -> [SeenStore.set] from
     *  either side can lose the other's write — dropping a markSeen() entry and re-announcing a
     *  run this window already announced, which is the exact failure this class exists to
     *  prevent. Never held across the getGraph fetch or the notify() call below — both can be
     *  arbitrarily slow (network, a flaky UI callback) and neither touches the seen map. */
    private val seenLock = Any()

    /** The current poll loop, if [start] has been called; cancelled by [stop]. Cancelling this Job
     *  is the Kotlin equivalent of the TS `loopEpoch` bump: a tick's blocking [poll] call keeps
     *  running to completion (it's not a suspension point), but once it returns, the loop's own
     *  `while (isActive)` check — now false — stops it from rescheduling, so a stop() (or a second
     *  start()) that lands mid-poll can never be undone by the tick it interrupted. */
    @Volatile private var loopJob: Job? = null

    /** Never let a throwing trace callback itself break the caller — trace is diagnostics only. */
    private fun traceSafe(l: String) {
        try {
            trace?.invoke(l)
        } catch (_: Throwable) {
            // diagnostics only
        }
    }

    private fun seenSnapshot(): Map<String, Long> {
        val cutoff = now() - ttlMs
        return seen.get().filterValues { it >= cutoff }
    }

    private fun fetchAwaiting(): List<Run>? {
        val c = client() ?: return null
        return try {
            c.listRuns(limit = 100, status = listOf("awaiting_approval"))
        } catch (e: Exception) {
            traceSafe("approvals poll failed: ${e.message ?: e}")
            null
        }
    }

    /** Record everything currently awaiting as seen, notifying nobody (first activation / sign-in). */
    fun prime() {
        primed = false
        val runs = fetchAwaiting() ?: return
        recordSilently(runs)
    }

    /** Swallow [runs] into the seen set and mark the watcher primed — but only once the write
     *  actually landed. A store we could not persist to would otherwise let the very next poll
     *  treat the whole backlog as fresh; and a rejecting store must never throw out of [prime]. */
    private fun recordSilently(runs: List<Run>) {
        synchronized(seenLock) {
            val map = seenSnapshot().toMutableMap()
            val t = now()
            for (r in runs) map.putIfAbsent(r.id, t)
            try {
                seen.set(map)
                primed = true
            } catch (e: Exception) {
                traceSafe("approvals prime seen.set failed: ${e.message ?: e}")
            }
        }
    }

    /** Mark one run as already announced. The run-output tail raises its own "awaiting approval"
     *  toast for runs started from this window; without this the poll loop would announce the very
     *  same run a second time. */
    fun markSeen(runId: String) {
        synchronized(seenLock) {
            val map = seenSnapshot().toMutableMap()
            map[runId] = now()
            try {
                seen.set(map)
            } catch (e: Exception) {
                traceSafe("approvals markSeen failed: ${e.message ?: e}")
            }
        }
    }

    /** Blocking, single-flight: a second call while one is in flight joins it rather than issuing a
     *  second request. */
    fun poll() {
        var mine = false
        val f = synchronized(pollLock) {
            inflight ?: CompletableFuture<Unit>().also {
                inflight = it
                mine = true
            }
        }
        if (mine) {
            try {
                doPoll()
            } finally {
                f.complete(Unit)
                synchronized(pollLock) { if (inflight === f) inflight = null }
            }
        }
        try {
            f.get()
        } catch (e: ExecutionException) {
            throw RuntimeException(e.cause ?: e)
        } catch (e: InterruptedException) {
            Thread.currentThread().interrupt()
            throw RuntimeException("interrupted while waiting for approvals poll", e)
        }
    }

    private fun doPoll() {
        val runs = fetchAwaiting() ?: return
        if (!primed) {
            recordSilently(runs)
            return
        }
        val fresh: List<Run>
        synchronized(seenLock) {
            val before = seen.get()
            val map = seenSnapshot().toMutableMap()
            fresh = runs.filter { it.id !in map }
            val t = now()
            for (r in fresh) map[r.id] = t
            if (fresh.isNotEmpty() || map.size != before.size) {
                // A rejecting persistence call must not stop the runs below from being notified,
                // nor take down the poll loop that called us.
                try {
                    seen.set(map)
                } catch (e: Exception) {
                    traceSafe("approvals seen.set failed: ${e.message ?: e}")
                }
            }
        }
        val c = client()
        for (r in fresh) {
            var summary: GraphSummary? = null
            if (c != null) {
                // Catches Throwable, not just Exception: an Error (e.g. the AssertionError
                // IntelliJ's LOG.error throws in test/EAP builds) must not escape here either —
                // this run is about to be marked seen either way, so letting an Error propagate
                // would both skip the rest of the batch AND make this run never get announced.
                try {
                    summary = c.getGraph(r.id).summary
                } catch (e: Throwable) {
                    summary = null
                    traceSafe("approvals getGraph failed: ${e.message ?: e}")
                }
            }
            // One run's notify() throwing (e.g. a flaky platform notification, or an assertion
            // firing in a test/EAP build) must not swallow the rest of this batch — each run gets
            // its own try/catch, over Throwable for the same reason as getGraph above.
            try {
                notify(ApprovalNotice(r, workspaceName(r.workspace_id), summary))
            } catch (e: Throwable) {
                traceSafe("approvals notify failed: ${e.message ?: e}")
            }
        }
    }

    fun start(intervalMs: Long) {
        stop()
        if (intervalMs <= 0) return
        loopJob = scope.launch(Dispatchers.IO) {
            while (isActive) {
                delay(intervalMs)
                if (!isActive) break
                try {
                    poll()
                } catch (e: Throwable) {
                    traceSafe("approvals poll failed: ${e.message ?: e}")
                }
            }
        }
    }

    fun stop() {
        loopJob?.cancel()
        loopJob = null
    }

    override fun dispose() {
        stop()
    }
}
