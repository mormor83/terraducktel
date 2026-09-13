package com.terraducktel.jetbrains.state

import com.intellij.openapi.Disposable
import com.intellij.openapi.components.Service
import com.intellij.openapi.components.service
import com.intellij.openapi.util.Disposer
import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.api.TdtClient
import com.terraducktel.jetbrains.api.Workspace
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.settings.TdtSettings
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicReference

/**
 * Application-level polling cache of the current BU's workspaces + runs — a coroutine port of
 * `services/vscode/src/state/store.ts`'s `Store` class. One in-flight [refresh] at a time; keeps
 * the last good snapshot on failure and backs off after repeated failures. Every public method is
 * safe to call from any thread; [refreshAndWait] blocks the calling thread (never call it from the
 * EDT) and [addListener] callbacks fire on whatever pooled thread completed the refresh — a UI
 * listener must marshal to the EDT itself.
 */
@Service(Service.Level.APP)
class Store(private val scope: CoroutineScope) : Disposable {

    @Volatile var workspaces: List<Workspace> = emptyList(); private set
    @Volatile var runs: List<Run> = emptyList(); private set
    @Volatile var lastError: Throwable? = null; private set
    @Volatile var consecutiveFailures: Int = 0; private set

    @Volatile private var byWs: Map<String, List<Run>> = emptyMap()

    /** Whether a view is on screen. The poll loop keeps ticking while inactive but skips the
     *  network: polling a tool window nobody is looking at is pure load on the API. A manual
     *  [refresh] is never gated by this. */
    @Volatile private var active: Boolean = true

    /** Single-flight guard for [refresh]: a Job in flight is handed back to every concurrent
     *  caller instead of starting a second fetch. Mutated only under `this`'s monitor. */
    private val inflightJob = AtomicReference<Job?>(null)

    /** The current poll loop, if [start] has been called; cancelled by [stop]. */
    @Volatile private var loopJob: Job? = null

    private val listeners = CopyOnWriteArrayList<() -> Unit>()

    /** Test seams — overridden by [com.terraducktel.jetbrains.session.TdtSessionTest]-style tests
     *  and by the plain-JUnit `StoreTest`, which constructs [Store] directly. */
    internal var clientProvider: () -> TdtClient? = { TdtSession.getInstance().clientOrNull() }
    internal var runsLimitProvider: () -> Int = { TdtSettings.getInstance().state.runsLimit }

    fun runsFor(wsId: String): List<Run> = byWs[wsId] ?: emptyList()
    fun workspace(id: String): Workspace? = workspaces.find { it.id == id }
    fun run(id: String): Run? = runs.find { it.id == id }

    /** Starts a refresh unless one is already in flight, in which case that job is returned
     *  instead. Safe to call from any thread; the returned [Job] runs on [Dispatchers.IO]. */
    @Synchronized
    fun refresh(): Job {
        inflightJob.get()?.let { if (it.isActive) return it }
        val job = scope.launch(Dispatchers.IO) { doRefresh() }
        inflightJob.set(job)
        job.invokeOnCompletion { inflightJob.compareAndSet(job, null) }
        return job
    }

    /** Blocking helper for callers off the EDT (actions, tests) that need the refresh to have
     *  landed before they proceed. */
    fun refreshAndWait() = runBlocking { refresh().join() }

    /** Refetches with whatever client is current when the fetch resolves. If the session swaps
     *  the client (profile/BU change, sign-out) while a fetch is in flight, the just-landed data
     *  belongs to the OLD client and must never be applied — instead retry immediately with the
     *  now-current client. Bounded because a client only changes a finite number of times per user
     *  action; the retry cap is just a backstop against a pathological provider that never settles. */
    private suspend fun doRefresh() {
        val maxRetries = 5
        var attempt = 0
        while (true) {
            val c = clientProvider()
            if (c == null) {
                clearSnapshot()
                fireChanged()
                return
            }
            val stale = { attempt < maxRetries && clientProvider() !== c }
            try {
                val ws = c.listWorkspaces()
                val rs = c.listRuns(limit = runsLimitProvider())
                if (stale()) { attempt++; continue } // a newer client took over while this fetch was in flight
                applySnapshot(ws, rs)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (stale()) { attempt++; continue } // stale error from a superseded client; retry with the current one
                lastError = e
                consecutiveFailures++
            }
            break
        }
        fireChanged()
    }

    private fun applySnapshot(ws: List<Workspace>, rs: List<Run>) {
        workspaces = ws
        val sorted = rs.sortedWith(compareByDescending { it.created_at ?: "" })
        runs = sorted
        val map = HashMap<String, MutableList<Run>>()
        for (r in sorted) map.getOrPut(r.workspace_id) { mutableListOf() }.add(r)
        byWs = map
        lastError = null
        consecutiveFailures = 0
    }

    private fun clearSnapshot() {
        workspaces = emptyList()
        runs = emptyList()
        byWs = emptyMap()
        lastError = null
        consecutiveFailures = 0
    }

    fun clear() {
        clearSnapshot()
        fireChanged()
    }

    fun setActive(active: Boolean) { this.active = active }
    fun isActive(): Boolean = active

    /** Polls every `intervalMs` while a view is visible; after 3 consecutive failures stretches to
     *  5 minutes until one succeeds. Cancels any previous loop first, so a fresh `start()` never
     *  double-polls alongside one left running from before. */
    fun start(intervalMs: Long) {
        stop()
        loopJob = scope.launch(Dispatchers.IO) {
            while (isActive) {
                delay(if (consecutiveFailures >= 3) 300_000L else intervalMs)
                if (active) refresh().join()
            }
        }
    }

    fun stop() {
        loopJob?.cancel()
        loopJob = null
    }

    /** Fires [l] on the calling (pooled) thread after every change; removed automatically when
     *  [parent] is disposed. */
    fun addListener(parent: Disposable, l: () -> Unit) {
        listeners += l
        Disposer.register(parent) { listeners -= l }
    }

    private fun fireChanged() {
        listeners.forEach { it() }
    }

    /** Used by Task 9's tree tests to seed a snapshot without going through a real [refresh]. */
    internal fun setSnapshotForTest(workspaces: List<Workspace>, runs: List<Run>) {
        applySnapshot(workspaces, runs)
        fireChanged()
    }

    /** Used by Task 9's tree tests to simulate a failed refresh (the warning [MessageNode] at the
     *  top of both trees) without going through a real [refresh]. */
    internal fun setLastErrorForTest(t: Throwable?) {
        lastError = t
        fireChanged()
    }

    override fun dispose() {
        stop()
    }

    companion object {
        fun getInstance(): Store = service()
    }
}
