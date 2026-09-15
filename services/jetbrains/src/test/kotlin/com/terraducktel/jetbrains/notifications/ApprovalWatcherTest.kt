package com.terraducktel.jetbrains.notifications

import com.terraducktel.jetbrains.api.GraphSummary
import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.api.TdtClient
import com.terraducktel.jetbrains.api.TdtJson
import com.terraducktel.jetbrains.api.TokenProvider
import com.terraducktel.jetbrains.testutil.StubServer
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.serialization.encodeToString
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.CyclicBarrier
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

private class FakeTokens : TokenProvider {
    override fun getAccessToken() = "t"
    override fun refreshAccessToken(): String? = "t"
    override fun hasCredential() = true
    override fun signOut() {}
}

private class MemSeenStore(initial: Map<String, Long> = emptyMap()) : SeenStore {
    @Volatile var value: Map<String, Long> = initial
    override fun get(): Map<String, Long> = value
    override fun set(v: Map<String, Long>) {
        value = v
    }
}

/** A [SeenStore] whose [get] can be told to pause the *next* call made from a specific thread —
 *  used to force a deterministic window, mid read-modify-write, in which a genuinely concurrent
 *  writer (were the caller not holding [ApprovalWatcher]'s internal seen-map lock) could interleave
 *  and clobber the other side's update. */
private class GatedSeenStore(initial: Map<String, Long> = emptyMap()) : SeenStore {
    @Volatile var value: Map<String, Long> = initial
    @Volatile private var gateThread: Thread? = null
    private val entered = CountDownLatch(1)
    private val release = CountDownLatch(1)

    fun pauseNextGetFrom(t: Thread) {
        gateThread = t
    }

    fun awaitEntered(timeoutMs: Long = 5_000): Boolean = entered.await(timeoutMs, TimeUnit.MILLISECONDS)

    fun release() = release.countDown()

    override fun get(): Map<String, Long> {
        if (Thread.currentThread() === gateThread) {
            gateThread = null
            entered.countDown()
            assertTrue("release latch was never opened", release.await(5, TimeUnit.SECONDS))
        }
        return value
    }

    override fun set(v: Map<String, Long>) {
        value = v
    }
}

/**
 * Plain-JUnit port of every case in `services/vscode/test/unit/approvals.test.ts`: prime records
 * the backlog and notifies nobody, the watcher stays silent until primed (whether from a failed
 * prime() or a poll() that beats a slow one), the 24h TTL prunes on read, a per-run getGraph
 * failure still raises a notice (with a null summary), one notify() throwing never stops the rest
 * of the batch, poll() is single-flight, markSeen() dedupes against the run-output tail's own
 * toast, and stop()/start() around the coroutine loop behave like `loopEpoch` in the TS original.
 */
class ApprovalWatcherTest {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var now: Long = 1_000_000L

    @Before
    fun setUp() {
        now = 1_000_000L
    }

    @After
    fun tearDown() {
        scope.cancel()
    }

    private fun client(url: String): TdtClient = TdtClient(url, "default", FakeTokens())

    private fun run(id: String, ws: String = "w1") =
        Run(id = id, workspace_id = ws, command = "apply", status = "awaiting_approval", created_at = "2026-09-12T10:00:00Z")

    private fun mk(
        c: TdtClient,
        seenStore: SeenStore = MemSeenStore(),
        ttlMs: Long = 24 * 3_600_000L,
        notify: (ApprovalNotice) -> Unit,
    ): ApprovalWatcher = ApprovalWatcher(
        client = { c },
        workspaceName = { if (it == "w1") "vpc" else it },
        notify = notify,
        seen = seenStore,
        now = { now },
        ttlMs = ttlMs,
        scope = scope,
    )

    private fun slowRuns(srv: StubServer, counter: AtomicInteger, delayMs: Long = 40) {
        srv.on("GET", "/api/v1/runs") { _, ex ->
            counter.incrementAndGet()
            Thread.sleep(delayMs)
            StubServer.respond(ex, 200, "[]")
        }
    }

    @Test
    fun `notifies once per new awaiting run, with the graph summary when available`() {
        StubServer().use { srv ->
            var awaiting = listOf<Run>()
            srv.on("GET", "/api/v1/runs") { _, ex -> StubServer.respond(ex, 200, TdtJson.encodeToString(awaiting)) }
            srv.json("GET", "/api/v1/runs/r1/graph", 200, """{"nodes":[],"edges":[],"summary":{"add":1,"change":2,"destroy":0}}""")
            val notices = mutableListOf<ApprovalNotice>()
            val w = mk(client(srv.url)) { notices += it }

            w.prime() // nothing awaiting yet: the watcher is primed and quiet
            awaiting = listOf(run("r1"))
            w.poll(); w.poll()
            assertEquals(listOf("r1"), notices.map { it.run.id })
            assertEquals("vpc", notices[0].workspaceName)
            assertEquals(GraphSummary(add = 1, change = 2, destroy = 0), notices[0].summary)

            awaiting = listOf(run("r1"), run("r2", "w2"))
            srv.json("GET", "/api/v1/runs/r2/graph", 500, """{"detail":"boom"}""")
            w.poll()
            assertEquals(listOf("r1", "r2"), notices.map { it.run.id })
            assertNull(notices[1].summary)

            val q = srv.calls("GET", "/api/v1/runs")[0].query!!
            assertTrue(q.contains("status=awaiting_approval"))
            assertTrue(q.contains("limit=100"))
        }
    }

    @Test
    fun `prime records the current set as seen without notifying`() {
        StubServer().use { srv ->
            srv.json(
                "GET", "/api/v1/runs", 200,
                TdtJson.encodeToString(listOf(run("r1"), run("r2"))),
            )
            val seen = MemSeenStore()
            val notices = mutableListOf<ApprovalNotice>()
            val w = mk(client(srv.url), seen) { notices += it }

            w.prime()
            w.poll()

            assertTrue(notices.isEmpty())
            assertEquals(listOf("r1", "r2"), seen.value.keys.sorted())
        }
    }

    @Test
    fun `persists seen ids with timestamps, prunes entries older than the TTL, and honours persisted state across instances`() {
        StubServer().use { srv ->
            var awaiting = listOf<Run>()
            srv.on("GET", "/api/v1/runs") { _, ex -> StubServer.respond(ex, 200, TdtJson.encodeToString(awaiting)) }
            srv.json("GET", "/api/v1/runs/r1/graph", 200, """{"nodes":[],"edges":[],"summary":{}}""")
            val seen = MemSeenStore(mapOf("old" to now - 25 * 3_600_000L, "fresh" to now - 3_600_000L))
            var notices = mutableListOf<ApprovalNotice>()
            val w1 = mk(client(srv.url), seen, 24 * 3_600_000L) { notices += it }

            w1.prime()
            awaiting = listOf(run("r1"))
            w1.poll()
            assertEquals(listOf("r1"), notices.map { it.run.id })
            assertEquals(now, seen.value["r1"])
            assertEquals(now - 3_600_000L, seen.value["fresh"])
            assertNull(seen.value["old"])

            notices = mutableListOf()
            // A new instance (window reload) with the same store. It primes on an empty backlog,
            // so the only thing that can keep it quiet about r1 below is the persisted entry.
            awaiting = listOf()
            val w2 = mk(client(srv.url), seen) { notices += it }
            w2.prime()
            awaiting = listOf(run("r1"))
            w2.poll()
            assertTrue(notices.isEmpty())
        }
    }

    @Test
    fun `is single-flight and silent on failures`() {
        StubServer().use { srv ->
            val inflight = AtomicInteger(0)
            val max = AtomicInteger(0)
            srv.on("GET", "/api/v1/runs") { _, ex ->
                val cur = inflight.incrementAndGet()
                max.updateAndGet { m -> maxOf(m, cur) }
                Thread.sleep(30)
                inflight.decrementAndGet()
                StubServer.respond(ex, 503, "{}")
            }
            val notices = mutableListOf<ApprovalNotice>()
            val w = mk(client(srv.url)) { notices += it }

            val pool = Executors.newFixedThreadPool(3)
            val ready = CountDownLatch(3)
            try {
                val futures = (1..3).map {
                    pool.submit {
                        ready.countDown()
                        ready.await(5, TimeUnit.SECONDS)
                        w.poll()
                    }
                }
                futures.forEach { it.get(5, TimeUnit.SECONDS) }
            } finally {
                pool.shutdown()
                assertTrue(pool.awaitTermination(5, TimeUnit.SECONDS))
            }

            assertEquals(1, max.get())
            // Three concurrent polls that merely serialised (rather than genuinely joining the
            // one in-flight request) would still leave max==1 but issue three requests.
            assertEquals(1, srv.calls("GET", "/api/v1/runs").size)
            assertTrue(notices.isEmpty())
        }
    }

    @Test
    fun `keeps notifying and polling when notify() throws`() {
        StubServer().use { srv ->
            var awaiting = listOf<Run>()
            srv.on("GET", "/api/v1/runs") { _, ex -> StubServer.respond(ex, 200, TdtJson.encodeToString(awaiting)) }
            for (id in listOf("r1", "r2", "r3")) {
                srv.json("GET", "/api/v1/runs/$id/graph", 200, """{"nodes":[],"edges":[],"summary":{}}""")
            }
            val c = client(srv.url)
            var notices = mutableListOf<ApprovalNotice>()
            var calls = 0
            val w = ApprovalWatcher(
                client = { c },
                workspaceName = { if (it == "w1") "vpc" else it },
                notify = { n -> calls++; if (calls == 1) throw RuntimeException("boom") else notices += n },
                seen = MemSeenStore(),
                now = { now },
                scope = scope,
            )

            w.prime()
            awaiting = listOf(run("r1"), run("r2", "w2"))
            w.poll()
            assertEquals(listOf("r2"), notices.map { it.run.id }) // r1's notify() threw, r2 still notified

            notices = mutableListOf()
            awaiting = listOf(run("r1"), run("r2", "w2"), run("r3", "w2"))
            w.poll() // the next poll() still runs
            assertEquals(listOf("r3"), notices.map { it.run.id })
        }
    }

    // --- timer epoch: stop() must win over an in-flight tick's reschedule ---------------------

    @Test
    fun `stop during an in-flight poll is not undone by the tick's reschedule`() {
        StubServer().use { srv ->
            val c = AtomicInteger(0)
            slowRuns(srv, c)
            val w = mk(client(srv.url)) { }

            w.start(5)
            Thread.sleep(20) // first tick fired; its request is in flight
            assertEquals(1, c.get())
            w.stop()
            Thread.sleep(60) // let the in-flight poll land and (not) re-arm
            val after = c.get()
            Thread.sleep(120)
            assertEquals(after, c.get()) // no request after the stop
            w.dispose()
        }
    }

    @Test
    fun `start() twice during an in-flight poll leaves a single loop that stop() kills`() {
        StubServer().use { srv ->
            val c = AtomicInteger(0)
            slowRuns(srv, c)
            val w = mk(client(srv.url)) { }

            w.start(5)
            Thread.sleep(20) // tick 1 in flight
            w.start(5) // re-arm mid-poll: must not leave two chains
            Thread.sleep(60)
            w.stop()
            Thread.sleep(60)
            val after = c.get()
            Thread.sleep(120)
            assertEquals(after, c.get()) // the orphan chain would keep polling every 5 ms
            w.dispose()
        }
    }

    /** [ApprovalWatcher.start]/[ApprovalWatcher.stop] both read-modify-write `loopJob` and are now
     *  `@Synchronized` — this drives two REAL concurrent threads through `start(5)` (a
     *  [CyclicBarrier] releases them together, rather than the sequential same-thread calls the
     *  test above makes) and asserts the platform invariant that actually matters: whatever loop
     *  survives, a single subsequent `stop()` can still reach and kill it. Without the
     *  synchronization, one thread's `stop()` (inside `start()`) can read `loopJob` before the
     *  other thread's `start()` assigns it, orphaning a loop nothing can cancel — which would show
     *  up here as requests still landing after `stop()`. */
    @Test
    fun `two concurrent start()s leave a single loop that a later stop() can still reach`() {
        StubServer().use { srv ->
            val c = AtomicInteger(0)
            srv.on("GET", "/api/v1/runs") { _, ex -> c.incrementAndGet(); StubServer.respond(ex, 200, "[]") }
            val w = mk(client(srv.url)) { }

            val barrier = CyclicBarrier(2)
            val threads = (1..2).map {
                Thread {
                    barrier.await()
                    w.start(5)
                }
            }
            threads.forEach { it.start() }
            threads.forEach { it.join(5_000) }
            threads.forEach { assertFalse("a start() thread never finished", it.isAlive) }

            Thread.sleep(60) // let whatever loop(s) survived the race tick a few times
            w.stop()
            Thread.sleep(60) // let an in-flight tick (if any) land and (not) re-arm
            val after = c.get()
            Thread.sleep(120)
            assertEquals("no request after stop() — an orphaned loop would keep polling every 5 ms", after, c.get())
            w.dispose()
        }
    }

    // --- silent until primed -------------------------------------------------------------------

    @Test
    fun `swallows the backlog on the first poll after prime() failed, then notifies normally`() {
        StubServer().use { srv ->
            val fail = AtomicBoolean(true)
            var awaiting = listOf(run("r1"), run("r2", "w2"))
            srv.on("GET", "/api/v1/runs") { _, ex ->
                if (fail.get()) StubServer.respond(ex, 503, "{}")
                else StubServer.respond(ex, 200, TdtJson.encodeToString(awaiting))
            }
            for (id in listOf("r1", "r2", "r3")) {
                srv.json("GET", "/api/v1/runs/$id/graph", 200, """{"nodes":[],"edges":[],"summary":{}}""")
            }
            val seen = MemSeenStore()
            val notices = mutableListOf<ApprovalNotice>()
            val w = mk(client(srv.url), seen) { notices += it }

            w.prime() // network down at wake-up
            fail.set(false)
            w.poll() // must NOT announce the whole backlog
            assertTrue(notices.isEmpty())
            assertEquals(listOf("r1", "r2"), seen.value.keys.sorted())

            awaiting = awaiting + run("r3", "w2")
            w.poll()
            assertEquals(listOf("r3"), notices.map { it.run.id })
        }
    }

    @Test
    fun `a poll whose response beats a slow prime()'s notifies nobody`() {
        StubServer().use { srv ->
            val req = AtomicInteger(0)
            srv.on("GET", "/api/v1/runs") { _, ex ->
                val n = req.getAndIncrement()
                if (n == 0) Thread.sleep(60) // the prime (first request) is the slow one
                StubServer.respond(ex, 200, TdtJson.encodeToString(listOf(run("r1"), run("r2", "w2"))))
            }
            for (id in listOf("r1", "r2")) {
                srv.json("GET", "/api/v1/runs/$id/graph", 200, """{"nodes":[],"edges":[],"summary":{}}""")
            }
            val notices = mutableListOf<ApprovalNotice>()
            val w = mk(client(srv.url)) { notices += it }

            val priming = Thread { w.prime() }
            priming.start()
            Thread.sleep(10)
            w.poll() // resolves first — and must stay silent
            assertTrue(notices.isEmpty())
            priming.join(5_000)
            assertFalse("prime() thread never finished", priming.isAlive)
            assertTrue(notices.isEmpty())
        }
    }

    @Test
    fun `a rejecting seen store cannot reject prime(), and leaves it unprimed`() {
        StubServer().use { srv ->
            srv.json("GET", "/api/v1/runs", 200, TdtJson.encodeToString(listOf(run("r1"))))
            srv.json("GET", "/api/v1/runs/r1/graph", 200, """{"nodes":[],"edges":[],"summary":{}}""")
            val traces = mutableListOf<String>()
            val seen = object : SeenStore {
                override fun get(): Map<String, Long> = emptyMap()
                override fun set(v: Map<String, Long>) {
                    throw RuntimeException("globalState is toast")
                }
            }
            val notices = mutableListOf<ApprovalNotice>()
            val w = ApprovalWatcher(
                client = { client(srv.url) },
                workspaceName = { it },
                notify = { notices += it },
                seen = seen,
                now = { now },
                trace = { traces += it },
                scope = scope,
            )

            w.prime()
            assertTrue(traces.any { it.contains("seen.set failed") })
            w.poll() // still unprimed: silent, never a spray
            assertTrue(notices.isEmpty())
        }
    }

    // --- dedupe against the run-output tail toast ----------------------------------------------

    @Test
    fun `markSeen() suppresses the poll notice for a run the run output already announced`() {
        StubServer().use { srv ->
            var awaiting = listOf<Run>()
            srv.on("GET", "/api/v1/runs") { _, ex -> StubServer.respond(ex, 200, TdtJson.encodeToString(awaiting)) }
            for (id in listOf("r8", "r9")) {
                srv.json("GET", "/api/v1/runs/$id/graph", 200, """{"nodes":[],"edges":[],"summary":{}}""")
            }
            val seen = MemSeenStore()
            val notices = mutableListOf<ApprovalNotice>()
            val w = mk(client(srv.url), seen) { notices += it }

            w.prime()
            w.markSeen("r9")
            awaiting = listOf(run("r9"), run("r8", "w2"))
            w.poll()
            assertEquals(listOf("r8"), notices.map { it.run.id }) // r9 was announced by the tail toast
            assertEquals(now, seen.value["r9"])
        }
    }

    @Test
    fun `does nothing without a client, and start(0) stops the timer`() {
        StubServer().use { srv ->
            val notices = mutableListOf<ApprovalNotice>()
            val w = ApprovalWatcher(
                client = { null },
                workspaceName = { it },
                notify = { notices += it },
                seen = MemSeenStore(),
                now = { now },
                scope = scope,
            )
            w.poll()
            assertTrue(notices.isEmpty())
            assertEquals(0, srv.calls.size)

            w.start(1)
            w.start(0)
            Thread.sleep(20)
            assertEquals(0, srv.calls.size)
            w.dispose()
        }
    }

    // --- concurrent markSeen() vs. the poll's own seen-map read-modify-write ------------------

    @Test
    fun `markSeen() from another thread cannot lose its write to a concurrent poll's seen-map update`() {
        StubServer().use { srv ->
            var awaiting = listOf<Run>()
            srv.on("GET", "/api/v1/runs") { _, ex -> StubServer.respond(ex, 200, TdtJson.encodeToString(awaiting)) }
            srv.json("GET", "/api/v1/runs/rOther/graph", 200, """{"nodes":[],"edges":[],"summary":{}}""")
            val seen = GatedSeenStore()
            val notices = mutableListOf<ApprovalNotice>()
            val w = mk(client(srv.url), seen) { notices += it }
            w.prime() // primed on an empty backlog, before rOther exists and before the gate is armed
            awaiting = listOf(run("rOther"))

            val pollThread = Thread { w.poll() }
            seen.pauseNextGetFrom(pollThread)
            pollThread.start()
            // Poll is now parked inside doPoll()'s synchronized seen-map section — holding the
            // lock, if the fix is in place — on its very first `seen.get()`.
            assertTrue("poll never reached the gated seen.get()", seen.awaitEntered())

            val markSeenDone = AtomicBoolean(false)
            val markSeenThread = Thread {
                w.markSeen("rX")
                markSeenDone.set(true)
            }
            markSeenThread.start()
            // Give markSeen() a real chance to run to completion if it weren't blocked on the
            // watcher's seen-map lock — this is the window in which the unsynchronised version of
            // doPoll()/markSeen() could interleave and clobber each other's write.
            Thread.sleep(150)
            assertFalse("markSeen() must not proceed while poll holds the seen-map lock", markSeenDone.get())
            assertFalse("markSeen()'s write must not land before poll's section releases the lock", seen.value.containsKey("rX"))

            seen.release() // let poll's paused read return; poll finishes its own read-modify-write
            pollThread.join(5_000)
            assertFalse("poll thread never finished", pollThread.isAlive)
            markSeenThread.join(5_000)
            assertFalse("markSeen thread never finished", markSeenThread.isAlive)

            // Both operations landed without clobbering each other: poll's own run was fresh at
            // the time it read the seen map (nobody had marked it) and was correctly notified;
            // markSeen()'s entry for the unrelated run was not lost to poll's overwrite.
            assertEquals(listOf("rOther"), notices.map { it.run.id })
            assertTrue(seen.value.containsKey("rOther"))
            assertTrue(seen.value.containsKey("rX"))
        }
    }
}
