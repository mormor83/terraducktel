package com.terraducktel.jetbrains.state

import com.intellij.openapi.Disposable
import com.intellij.openapi.components.Service
import com.intellij.openapi.components.service
import com.intellij.openapi.diagnostic.ControlFlowException
import com.intellij.openapi.util.Disposer
import com.terraducktel.jetbrains.TdtLog
import com.terraducktel.jetbrains.api.BusinessUnit
import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.api.TdtClient
import com.terraducktel.jetbrains.api.Workspace
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.settings.TdtSettings
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicReference

/** One business unit's slice of the [Store]: its workspaces + runs as of the last fetch that
 *  succeeded, plus [error] when the most recent fetch for THIS BU failed (the last good data stays
 *  alongside it) and [loaded] = false until the first fetch for it has landed. */
data class BuState(
    val bu: BusinessUnit,
    val workspaces: List<Workspace> = emptyList(),
    val runs: List<Run> = emptyList(),
    val error: String? = null,
    val loaded: Boolean = false,
) {
    /** Runs (newest first, as stored) grouped by workspace id. */
    val runsByWorkspace: Map<String, List<Run>> by lazy {
        val map = HashMap<String, MutableList<Run>>()
        for (r in runs) map.getOrPut(r.workspace_id) { mutableListOf() }.add(r)
        map
    }
}

data class WorkspaceRef(val ws: Workspace, val bu: BusinessUnit)
data class RunRef(val run: Run, val bu: BusinessUnit)

/**
 * Application-level polling cache of every VISIBLE business unit's workspaces + runs — a coroutine
 * port of `services/vscode/src/state/store.ts`'s `Store` class. Every poll fetches the BU list
 * once, then workspaces + runs per visible BU (each with that BU's `X-Business-Unit` header, at
 * most [BU_FETCH_CONCURRENCY] requests in flight). One in-flight [refresh] at a time; a failure for
 * one BU is recorded on that BU only and never blanks the others; a failure of the BU list itself
 * keeps the last snapshot and backs off after repeated failures. Every public method is safe to
 * call from any thread; [refreshAndWait] blocks the calling thread (never call it from the EDT)
 * and [addListener] callbacks fire on whatever pooled thread completed the refresh — a UI listener
 * must marshal to the EDT itself.
 */
@Service(Service.Level.APP)
class Store(private val scope: CoroutineScope) : Disposable {

    /** Every business unit the signed-in user can access, as of the last successful list fetch. */
    @Volatile var businessUnits: List<BusinessUnit> = emptyList(); private set

    /** Global failure (the BU list could not be fetched); per-BU failures live in [BuState.error]. */
    @Volatile var lastError: Throwable? = null; private set
    @Volatile var consecutiveFailures: Int = 0; private set

    @Volatile private var perBu: Map<String, BuState> = emptyMap()

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

    /** Hidden BU slugs of the active profile — read live on every refresh and every tree redraw. */
    internal var hiddenProvider: () -> Set<String> = {
        TdtSession.getInstance().profile?.name?.let { TdtSettings.getInstance().hiddenBuSlugs(it) } ?: emptySet()
    }

    /** Persists a new hidden set (default: the active profile's entry in [TdtSettings]). */
    internal var hiddenWriter: (Set<String>) -> Unit = { hidden ->
        TdtSession.getInstance().profile?.name?.let { TdtSettings.getInstance().setHiddenBuSlugs(it, hidden) }
    }

    /** Shows exactly [selectedSlugs] (of the known business units) and hides the rest. Rejected —
     *  returns false and changes nothing — when none of them is a known business unit. On success
     *  the choice is persisted, listeners hear about it at once (the trees redraw from the stored
     *  list without waiting for the network) and a refresh is started so newly shown BUs load. */
    fun applyFilter(selectedSlugs: Set<String>): Boolean {
        val hidden = BuFilter.hiddenForSelection(businessUnits, selectedSlugs) ?: return false
        hiddenWriter(hidden)
        fireChanged()
        refresh()
        return true
    }

    /** The business units currently shown (accessible and not hidden), sorted by name. */
    fun visibleBus(): List<BusinessUnit> =
        BuFilter.visible(businessUnits, hiddenProvider()).sortedWith(compareBy({ it.name.lowercase() }, { it.slug }))

    /** The snapshot of one visible BU; null for a hidden / unknown slug. */
    fun buState(slug: String): BuState? = if (visibleBus().any { it.slug == slug }) perBu[slug] else null

    /** Snapshots of the visible BUs (one not fetched yet appears as an unloaded [BuState]). */
    fun visibleStates(): List<BuState> = visibleBus().map { perBu[it.slug] ?: BuState(it) }

    fun allWorkspaces(): List<WorkspaceRef> = visibleStates().flatMap { s -> s.workspaces.map { WorkspaceRef(it, s.bu) } }
    fun allRuns(): List<RunRef> = visibleStates().flatMap { s -> s.runs.map { RunRef(it, s.bu) } }

    /** Looks across every loaded BU. */
    fun findWorkspace(id: String): WorkspaceRef? =
        perBu.values.firstNotNullOfOrNull { s -> s.workspaces.find { it.id == id }?.let { WorkspaceRef(it, s.bu) } }

    fun findRun(id: String): RunRef? =
        perBu.values.firstNotNullOfOrNull { s -> s.runs.find { it.id == id }?.let { RunRef(it, s.bu) } }

    /** Flat views over the visible BUs, for callers that only need the entities themselves. */
    val workspaces: List<Workspace> get() = allWorkspaces().map { it.ws }
    val runs: List<Run> get() = allRuns().map { it.run }
    fun workspace(id: String): Workspace? = findWorkspace(id)?.ws
    fun run(id: String): Run? = findRun(id)?.run

    /** The runs of workspace [wsId] (newest first), wherever it lives. */
    fun runsFor(wsId: String): List<Run> =
        perBu.values.firstNotNullOfOrNull { it.runsByWorkspace[wsId] } ?: emptyList()

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

    /** Refetches with whatever client / filter is current when the fetch resolves. If the session
     *  swaps the client (profile change, sign-out) or the user changes the BU filter while a fetch
     *  is in flight, the just-landed data belongs to the OLD client/filter and must never be
     *  applied — instead retry immediately with the current ones. Bounded because those change a
     *  finite number of times per user action; the retry cap is just a backstop against a
     *  pathological provider that never settles. */
    private suspend fun doRefresh() {
        val maxRetries = 5
        var attempt = 0
        while (true) {
            var c: TdtClient? = null
            try {
                // clientProvider() itself lives inside the try: a settings/session read is not
                // expected to throw, but if it ever does, that failure is recorded exactly like a
                // network failure below rather than crashing this coroutine outright.
                val current = clientProvider().also { c = it } ?: run {
                    clearSnapshot()
                    fireChanged()
                    return
                }
                val hidden = hiddenProvider()
                val units = current.listBusinessUnits()
                val shown = BuFilter.visible(units, hidden)
                val results = fetchBus(current, shown)
                if (attempt < maxRetries && (clientProvider() !== current || hiddenProvider() != hidden)) {
                    attempt++; continue // a newer client / filter took over while this fetch was in flight
                }
                applySnapshot(units, results)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // ControlFlowException (e.g. ProcessCanceledException) is a marker interface, not
                // itself a Throwable subtype, so it can't be a catch clause on its own — recognise
                // it here and rethrow before it's ever recorded as a fetch failure.
                if (e is ControlFlowException) throw e
                if (c != null && attempt < maxRetries && clientProvider() !== c) {
                    attempt++; continue // stale error from a superseded client; retry with the current one
                }
                lastError = e
                consecutiveFailures++
            }
            break
        }
        fireChanged()
    }

    /** Fetches workspaces then runs for every BU in [shown], with that BU's header. The per-BU
     *  fetches run concurrently, but a [Semaphore] caps the requests in flight at
     *  [BU_FETCH_CONCURRENCY]; one BU's failure is returned as its [Result], never thrown. */
    private suspend fun fetchBus(current: TdtClient, shown: List<BusinessUnit>): Map<String, Result<Pair<List<Workspace>, List<Run>>>> {
        val permits = Semaphore(BU_FETCH_CONCURRENCY)
        val limit = runsLimitProvider()
        return coroutineScope {
            shown.map { bu ->
                async(Dispatchers.IO) {
                    val scoped = current.withBu(bu.slug)
                    bu.slug to try {
                        val ws = permits.withPermit { scoped.listWorkspaces() }
                        val rs = permits.withPermit { scoped.listRuns(limit = limit) }
                        Result.success(ws to rs)
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: Exception) {
                        if (e is ControlFlowException) throw e
                        Result.failure(e)
                    }
                }
            }.awaitAll().toMap()
        }
    }

    /** Lands one successful BU-list fetch: BUs not in [units] or not fetched (hidden) are dropped;
     *  a BU whose own fetch failed keeps its previous data and carries the error. */
    private fun applySnapshot(units: List<BusinessUnit>, results: Map<String, Result<Pair<List<Workspace>, List<Run>>>>) {
        val previous = perBu
        val next = HashMap<String, BuState>()
        for ((slug, result) in results) {
            val bu = units.first { it.slug == slug }
            val old = previous[slug]
            next[slug] = result.fold(
                onSuccess = { (ws, rs) ->
                    BuState(bu, ws, rs.sortedWith(compareByDescending { it.created_at ?: "" }), error = null, loaded = true)
                },
                onFailure = { e -> (old ?: BuState(bu)).copy(bu = bu, error = e.message ?: e.javaClass.simpleName) },
            )
        }
        businessUnits = units
        perBu = next
        lastError = null
        // A poll counts as failed only when every visible BU failed (nothing at all got through);
        // a single failing BU is shown on that BU and must not push the whole poll into back-off.
        val allFailed = results.isNotEmpty() && results.values.all { it.isFailure }
        consecutiveFailures = if (allFailed) consecutiveFailures + 1 else 0
    }

    private fun clearSnapshot() {
        businessUnits = emptyList()
        perBu = emptyMap()
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

    /** Each listener runs in its own try/catch: one misbehaving subscriber (e.g. a tree rebuild
     *  throwing on unexpected data) must never stop the rest from hearing about the change. */
    private fun fireChanged() {
        for (l in listeners) {
            try {
                l()
            } catch (e: CancellationException) {
                throw e
            } catch (t: Throwable) {
                TdtLog.LOG.warn("Terraducktel: a Store listener threw", t)
            }
        }
    }

    /** Seeds a snapshot without going through a real [refresh]: [states] become the whole BU list
     *  and every BU's data. */
    internal fun setSnapshotForTest(states: List<BuState>) {
        businessUnits = states.map { it.bu }
        perBu = states.associateBy { it.bu.slug }
        lastError = null
        consecutiveFailures = 0
        fireChanged()
    }

    /** Single-BU convenience over [setSnapshotForTest]: everything lives in [bu]. Seeding nothing
     *  at all (no workspaces, no runs) means "no business units", i.e. an empty store. */
    internal fun setSnapshotForTest(workspaces: List<Workspace>, runs: List<Run>, bu: BusinessUnit = TEST_BU) {
        if (workspaces.isEmpty() && runs.isEmpty()) {
            setSnapshotForTest(emptyList<BuState>())
            return
        }
        setSnapshotForTest(
            listOf(BuState(bu, workspaces, runs.sortedWith(compareByDescending { it.created_at ?: "" }), loaded = true)),
        )
    }

    /** Simulates a failed BU-list refresh (the warning row at the top of both trees) without going
     *  through a real [refresh]. */
    internal fun setLastErrorForTest(t: Throwable?) {
        lastError = t
        fireChanged()
    }

    override fun dispose() {
        stop()
    }

    companion object {
        /** At most this many requests are in flight at the same time during one refresh. */
        const val BU_FETCH_CONCURRENCY = 4

        internal val TEST_BU = BusinessUnit("bu-test", "bu", "Test BU")

        fun getInstance(): Store = service()
    }
}
