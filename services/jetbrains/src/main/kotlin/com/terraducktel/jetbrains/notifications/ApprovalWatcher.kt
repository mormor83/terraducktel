package com.terraducktel.jetbrains.notifications

import com.intellij.openapi.Disposable
import com.terraducktel.jetbrains.api.BusinessUnit
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
data class ApprovalNotice(val run: Run, val workspaceName: String, val summary: GraphSummary?, val bu: BusinessUnit)

/**
 * Polls for runs awaiting approval and raises each one once (per 24h, across reloads/restarts). A
 * line-for-line port of `services/vscode/src/notifications/approvals.ts`'s `ApprovalWatcher`. Pure:
 * no IntelliJ UI here — the real `notify`/`workspaceName` plumbing (balloons, tool window lookups)
 * is Task 4's job. Every public method (other than [start]/[stop]/[dispose]) is blocking; call off
 * the EDT.
 */
class ApprovalWatcher(
    private val client: () -> TdtClient?,
    // The business units to watch (the ones the user has not hidden), evaluated on every poll.
    private val businessUnits: () -> List<BusinessUnit>,
    private val workspaceName: (String) -> String,
    private val notify: (ApprovalNotice) -> Unit,
    // Called once per poll, after every fresh run in the batch has been offered to [notify] — never
    // once per notice. A caller that pokes some other refresh (e.g. ApprovalService re-pulling the
    // Runs section's count pill) only needs to know "did this poll find anything new", not be re-run
    // once per run in a batch of several.
    private val onBatchNotified: () -> Unit = {},
    private val seen: SeenStore,
    private val now: () -> Long = System::currentTimeMillis,
    private val trace: ((String) -> Unit)? = null,
    private val ttlMs: Long = 24 * 3_600_000,
    private val scope: CoroutineScope,
) : Disposable {

    /** Slugs of the business units whose current backlog has been recorded as seen. Priming is
     *  tracked PER BU (as in the VS Code extension): a BU not in this set never notifies — its
     *  first successful fetch records the backlog silently and adds it. That covers a BU whose
     *  [prime] failed, a BU that just became visible, and a [poll] that lands before a concurrent
     *  [prime]. One BU that keeps failing therefore never silences the others. */
    private val primedBus: MutableSet<String> = java.util.concurrent.ConcurrentHashMap.newKeySet()

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

    /** What one sweep over the visible business units found: every awaiting run with the BU it
     *  belongs to, and the slugs of the BUs that actually answered. */
    private class Sweep(val runs: List<Pair<BusinessUnit, Run>>, val answered: Set<String>)

    /** One `/runs?status=awaiting_approval` request per visible BU, each with that BU's header.
     *  Null when there is nothing to ask (no client, no BU known yet) or every BU failed. A BU that
     *  fails alone is traced and skipped — the others still count. */
    private fun fetchAwaiting(): Sweep? {
        val c = client() ?: return null
        val bus = businessUnits()
        if (bus.isEmpty()) return null
        // A BU that is no longer visible forgets its priming, so it is swallowed again if it returns.
        primedBus.retainAll(bus.map { it.slug }.toSet())
        val found = mutableListOf<Pair<BusinessUnit, Run>>()
        val answered = mutableSetOf<String>()
        for (bu in bus) {
            try {
                val runs = c.withBu(bu.slug).listRuns(limit = 100, status = listOf("awaiting_approval"))
                for (r in runs) found += bu to r
                answered += bu.slug
            } catch (e: Throwable) {
                // Throwable, not just Exception — consistent with getGraph/notify below: an Error
                // (e.g. the AssertionError IntelliJ's LOG.error throws in test/EAP builds) must not
                // escape here either, or it takes down the poll loop that called this.
                traceSafe("approvals poll failed for ${bu.slug}: ${e.message ?: e}")
            }
        }
        if (answered.isEmpty()) return null
        return Sweep(found, answered)
    }

    /** Record everything currently awaiting as seen, notifying nobody (first activation / sign-in /
     *  filter change). Only the BUs that answered count as primed: a failing BU's backlog is still
     *  unknown, so it is swallowed on its own first successful fetch instead. */
    fun prime() {
        primedBus.clear()
        val sweep = fetchAwaiting() ?: return
        recordSilently(sweep.runs.map { it.second }, sweep.answered)
    }

    /** Swallow [runs] into the seen set and mark [answered] BUs primed — but only once the write
     *  actually landed. A store we could not persist to would otherwise let the very next poll
     *  treat the whole backlog as fresh; and a rejecting store must never throw out of [prime]. */
    private fun recordSilently(runs: List<Run>, answered: Set<String>) {
        synchronized(seenLock) {
            val map = seenSnapshot().toMutableMap()
            val t = now()
            for (r in runs) map.putIfAbsent(r.id, t)
            try {
                seen.set(map)
                primedBus += answered
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
        val sweep = fetchAwaiting() ?: return
        val runs = sweep.runs
        // Runs of BUs not yet primed are swallowed silently; only primed BUs can notify.
        val unprimed = sweep.answered - primedBus
        if (unprimed.isNotEmpty()) {
            recordSilently(runs.filter { it.first.slug in unprimed }.map { it.second }, unprimed)
        }
        val candidates = runs.filter { it.first.slug !in unprimed }
        val fresh: List<Pair<BusinessUnit, Run>>
        synchronized(seenLock) {
            val before = seen.get()
            val map = seenSnapshot().toMutableMap()
            fresh = candidates.filter { it.second.id !in map }
            val t = now()
            for ((_, r) in fresh) map[r.id] = t
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
        for ((bu, r) in fresh) {
            var summary: GraphSummary? = null
            if (c != null) {
                // Catches Throwable, not just Exception: an Error (e.g. the AssertionError
                // IntelliJ's LOG.error throws in test/EAP builds) must not escape here either —
                // this run is about to be marked seen either way, so letting an Error propagate
                // would both skip the rest of the batch AND make this run never get announced.
                try {
                    summary = c.withBu(bu.slug).getGraph(r.id).summary
                } catch (e: Throwable) {
                    summary = null
                    traceSafe("approvals getGraph failed: ${e.message ?: e}")
                }
            }
            // One run's notify() throwing (e.g. a flaky platform notification, or an assertion
            // firing in a test/EAP build) must not swallow the rest of this batch — each run gets
            // its own try/catch, over Throwable for the same reason as getGraph above.
            try {
                notify(ApprovalNotice(r, workspaceName(r.workspace_id), summary, bu))
            } catch (e: Throwable) {
                traceSafe("approvals notify failed: ${e.message ?: e}")
            }
        }
        // Once per poll that actually found something new — not once per run in [fresh] — so a
        // caller wiring this to e.g. a tool-window refresh doesn't redo it N times for a batch of N.
        if (fresh.isNotEmpty()) onBatchNotified()
    }

    // start()/stop() both read-modify-write loopJob; unsynchronized, two concurrent start()s (or a
    // start() racing a stop()) could interleave so one call's stop() reads loopJob before the
    // other's start() assigns it — orphaning a loop that then polls forever with nothing able to
    // reach it. Synchronized on `this` closes that window; neither method does I/O itself (the
    // launched coroutine body isn't run under this lock), so this never blocks on network calls.
    @Synchronized
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

    @Synchronized
    fun stop() {
        loopJob?.cancel()
        loopJob = null
    }

    override fun dispose() {
        stop()
    }
}
