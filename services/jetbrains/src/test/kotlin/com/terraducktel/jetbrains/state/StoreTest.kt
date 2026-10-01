package com.terraducktel.jetbrains.state

import com.intellij.openapi.util.Disposer
import com.terraducktel.jetbrains.api.TdtClient
import com.terraducktel.jetbrains.api.TokenProvider
import com.terraducktel.jetbrains.testutil.StubServer
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

private class FakeTokens : TokenProvider {
    override fun getAccessToken() = "t"
    override fun refreshAccessToken(): String? = "t"
    override fun hasCredential() = true
    override fun signOut() {}
}

/**
 * Plain-JUnit coverage of [Store]: the behaviours of `services/vscode/test/unit/store.test.ts`
 * (single-flight refresh, failure keeps the last snapshot, `stop()` during an in-flight tick
 * doesn't re-arm, a client swap mid-fetch is discarded and retried, listeners, `setActive()`,
 * `runsLimit` read live) plus the business-unit tree's own: workspaces and runs are fetched per
 * VISIBLE BU with that BU's `X-Business-Unit` header (never for hidden ones), at most four requests
 * at a time, one BU's failure never blanks the others, a BU that leaves the list is dropped, and a
 * fetch superseded by a client or filter change is discarded. Constructs [Store] directly (no
 * platform) and drives it against a real [TdtClient] + [StubServer], using the `clientProvider` /
 * `runsLimitProvider` / `hiddenProvider` seams instead of the session and settings services.
 */
class StoreTest {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    @After
    fun tearDown() {
        scope.cancel()
    }

    private fun client(url: String) = TdtClient(url, "", FakeTokens())

    private fun newStore(srv: StubServer, hidden: () -> Set<String> = { emptySet() }): Store {
        val store = Store(scope)
        val c = client(srv.url) // one stable instance: a fresh client per call would trip the stale-client check
        store.clientProvider = { c }
        store.runsLimitProvider = { 50 }
        store.hiddenProvider = hidden
        return store
    }

    private fun buJson(vararg slugs: String) =
        slugs.joinToString(prefix = "[", postfix = "]") { """{"id":"id-$it","slug":"$it","name":"${it.replaceFirstChar(Char::uppercase)}"}""" }

    /** `/business-units` lists [slugs]; each BU serves one workspace `w-<slug>` and one run `r-<slug>`,
     *  chosen by the request's `X-Business-Unit` header. */
    private fun serveBus(srv: StubServer, vararg slugs: String) {
        srv.json("GET", "/api/v1/business-units", 200, buJson(*slugs))
        srv.on("GET", "/api/v1/workspaces") { call, ex ->
            val bu = call.headers["x-business-unit"]
            StubServer.respond(ex, 200, """[{"id":"w-$bu","name":"ws of $bu"}]""")
        }
        srv.on("GET", "/api/v1/runs") { call, ex ->
            val bu = call.headers["x-business-unit"]
            StubServer.respond(ex, 200, """[{"id":"r-$bu","workspace_id":"w-$bu","command":"plan","status":"planned"}]""")
        }
    }

    // ─── per-BU fetching ─────────────────────────────────────────────────────────────

    @Test
    fun `fetches workspaces and runs once per visible BU, each with that BU's header`() {
        StubServer().use { srv ->
            serveBus(srv, "infra", "apps")
            val store = newStore(srv)

            store.refreshAndWait()

            assertEquals(listOf("apps", "infra"), store.visibleBus().map { it.slug }) // sorted by name
            assertEquals(listOf("w-infra"), store.buState("infra")!!.workspaces.map { it.id })
            assertEquals(listOf("r-infra"), store.buState("infra")!!.runs.map { it.id })
            assertEquals(listOf("w-apps"), store.buState("apps")!!.workspaces.map { it.id })
            assertTrue(store.buState("apps")!!.loaded)
            assertEquals(
                setOf("infra", "apps"),
                srv.calls("GET", "/api/v1/workspaces").map { it.headers["x-business-unit"] }.toSet(),
            )
            assertEquals(
                setOf("infra", "apps"),
                srv.calls("GET", "/api/v1/runs").map { it.headers["x-business-unit"] }.toSet(),
            )
        }
    }

    @Test
    fun `the BU list is fetched once per refresh, before any per-BU request, without a BU header`() {
        StubServer().use { srv ->
            serveBus(srv, "infra", "apps")
            val store = newStore(srv)

            store.refreshAndWait()

            assertEquals(1, srv.calls("GET", "/api/v1/business-units").size)
            assertNull(srv.calls("GET", "/api/v1/business-units").single().headers["x-business-unit"])
            assertEquals("/api/v1/business-units", srv.calls.first().path)
            store.refreshAndWait()
            assertEquals(2, srv.calls("GET", "/api/v1/business-units").size)
        }
    }

    @Test
    fun `hidden BUs are not fetched and are not visible`() {
        StubServer().use { srv ->
            serveBus(srv, "infra", "apps", "data")
            val store = newStore(srv, hidden = { setOf("apps") })

            store.refreshAndWait()

            assertEquals(listOf("data", "infra"), store.visibleBus().map { it.slug })
            assertNull(store.buState("apps"))
            assertEquals(
                setOf("infra", "data"),
                srv.calls("GET", "/api/v1/workspaces").map { it.headers["x-business-unit"] }.toSet(),
            )
            assertEquals(setOf("w-infra", "w-data"), store.allWorkspaces().map { it.ws.id }.toSet())
        }
    }

    @Test
    fun `at most four requests are in flight at once`() {
        StubServer().use { srv ->
            val inflight = AtomicInteger()
            val max = AtomicInteger()
            val slugs = (1..9).map { "bu$it" }
            srv.json("GET", "/api/v1/business-units", 200, buJson(*slugs.toTypedArray()))
            for (path in listOf("/api/v1/workspaces", "/api/v1/runs")) {
                srv.on("GET", path) { _, ex ->
                    max.accumulateAndGet(inflight.incrementAndGet(), ::maxOf)
                    Thread.sleep(40)
                    inflight.decrementAndGet()
                    StubServer.respond(ex, 200, "[]")
                }
            }
            val store = newStore(srv)

            store.refreshAndWait()

            assertEquals(9, store.visibleBus().size)
            assertEquals(18, srv.calls.size - 1) // every BU fetched both lists
            assertTrue("expected concurrency, got a max of ${max.get()}", max.get() >= 2)
            assertTrue("expected at most 4 in flight, got ${max.get()}", max.get() <= 4)
        }
    }

    // ─── isolation ───────────────────────────────────────────────────────────────────

    @Test
    fun `one BU failing is recorded on that BU only and the others keep their data`() {
        StubServer().use { srv ->
            serveBus(srv, "infra", "apps")
            srv.on("GET", "/api/v1/workspaces") { call, ex ->
                val bu = call.headers["x-business-unit"]
                if (bu == "apps") StubServer.respond(ex, 403, """{"detail":"nope"}""")
                else StubServer.respond(ex, 200, """[{"id":"w-$bu","name":"ws of $bu"}]""")
            }
            val store = newStore(srv)

            store.refreshAndWait()

            assertEquals(listOf("w-infra"), store.buState("infra")!!.workspaces.map { it.id })
            assertNull(store.buState("infra")!!.error)
            val apps = store.buState("apps")!!
            assertNotNull(apps.error)
            assertTrue(apps.error!!.contains("nope"))
            assertNull("a per-BU failure must not become the global error", store.lastError)
            assertEquals(0, store.consecutiveFailures)
        }
    }

    @Test
    fun `a BU that fails after succeeding keeps its last good data and shows the error`() {
        StubServer().use { srv ->
            serveBus(srv, "infra", "apps")
            val store = newStore(srv)
            store.refreshAndWait()
            assertEquals(listOf("w-apps"), store.buState("apps")!!.workspaces.map { it.id })

            srv.on("GET", "/api/v1/workspaces") { call, ex ->
                val bu = call.headers["x-business-unit"]
                if (bu == "apps") StubServer.respond(ex, 500, """{"detail":"db down"}""")
                else StubServer.respond(ex, 200, """[{"id":"w2-$bu","name":"ws of $bu"}]""")
            }
            store.refreshAndWait()

            val apps = store.buState("apps")!!
            assertEquals(listOf("w-apps"), apps.workspaces.map { it.id }) // last good data kept
            assertNotNull(apps.error)
            assertEquals(listOf("w2-infra"), store.buState("infra")!!.workspaces.map { it.id }) // others moved on
        }
    }

    @Test
    fun `a BU that leaves the list is dropped from the store`() {
        StubServer().use { srv ->
            serveBus(srv, "infra", "apps")
            val store = newStore(srv)
            store.refreshAndWait()
            assertNotNull(store.findWorkspace("w-apps"))

            srv.json("GET", "/api/v1/business-units", 200, buJson("infra"))
            store.refreshAndWait()

            assertEquals(listOf("infra"), store.businessUnits.map { it.slug })
            assertNull(store.buState("apps"))
            assertNull(store.findWorkspace("w-apps"))
            assertNull(store.findRun("r-apps"))
        }
    }

    @Test
    fun `a failed BU list fetch keeps the last snapshot, sets the global error and counts a failure`() {
        StubServer().use { good ->
            serveBus(good, "infra")
            val dead = StubServer()
            dead.close() // closed before first use: every request to it fails immediately

            val store = Store(scope)
            var useGood = true
            val goodClient = client(good.url)
            val deadClient = client(dead.url)
            store.clientProvider = { if (useGood) goodClient else deadClient }
            store.runsLimitProvider = { 50 }
            store.hiddenProvider = { emptySet() }

            store.refreshAndWait()
            assertEquals(listOf("w-infra"), store.workspaces.map { it.id })
            assertEquals(0, store.consecutiveFailures)

            useGood = false
            store.refreshAndWait()

            assertEquals(listOf("w-infra"), store.workspaces.map { it.id }) // last good snapshot kept
            assertEquals(1, store.consecutiveFailures)
            assertNotNull(store.lastError)
        }
    }

    // ─── lookups ─────────────────────────────────────────────────────────────────────

    @Test
    fun `findWorkspace and findRun resolve across every BU and report the owning BU`() {
        StubServer().use { srv ->
            serveBus(srv, "infra", "apps")
            val store = newStore(srv)
            store.refreshAndWait()

            val ws = store.findWorkspace("w-apps")!!
            assertEquals("apps", ws.bu.slug)
            assertEquals("w-apps", ws.ws.id)
            val run = store.findRun("r-infra")!!
            assertEquals("infra", run.bu.slug)
            assertEquals(listOf("r-infra"), store.runsFor("w-infra").map { it.id })
            assertNull(store.findWorkspace("nope"))
            assertNull(store.findRun("nope"))
        }
    }

    // ─── superseded refreshes ────────────────────────────────────────────────────────

    @Test
    fun `a client swap mid-fetch discards the stale response and retries with the new client`() {
        StubServer().use { srvA ->
            StubServer().use { srvB ->
                val requestArrived = CountDownLatch(1)
                val release = CountDownLatch(1)
                srvA.json("GET", "/api/v1/business-units", 200, buJson("a"))
                srvA.on("GET", "/api/v1/workspaces") { _, ex ->
                    requestArrived.countDown()
                    assertTrue("release latch was never opened", release.await(5, TimeUnit.SECONDS))
                    StubServer.respond(ex, 200, """[{"id":"a","name":"a"}]""")
                }
                srvA.json("GET", "/api/v1/runs", 200, "[]")
                srvB.json("GET", "/api/v1/business-units", 200, buJson("b"))
                srvB.json("GET", "/api/v1/workspaces", 200, """[{"id":"b","name":"b"}]""")
                srvB.json("GET", "/api/v1/runs", 200, "[]")

                val clientA = client(srvA.url)
                val clientB = client(srvB.url)
                var current: TdtClient = clientA
                val store = Store(scope)
                store.clientProvider = { current }
                store.runsLimitProvider = { 50 }
                store.hiddenProvider = { emptySet() }

                val job = store.refresh()
                assertTrue("request never reached the (blocked) server", requestArrived.await(5, TimeUnit.SECONDS))
                current = clientB // swap while A's request is still in flight
                release.countDown()
                runBlocking { job.join() }

                assertEquals(listOf("b"), store.workspaces.map { it.id })
                assertEquals(listOf("b"), store.businessUnits.map { it.slug })
            }
        }
    }

    @Test
    fun `a filter change mid-fetch discards the stale response and retries with the new filter`() {
        StubServer().use { srv ->
            val requestArrived = CountDownLatch(1)
            val release = CountDownLatch(1)
            srv.json("GET", "/api/v1/business-units", 200, buJson("infra", "apps"))
            srv.on("GET", "/api/v1/workspaces") { call, ex ->
                val bu = call.headers["x-business-unit"]
                if (bu == "infra" && requestArrived.count > 0) {
                    requestArrived.countDown()
                    assertTrue("release latch was never opened", release.await(5, TimeUnit.SECONDS))
                }
                StubServer.respond(ex, 200, """[{"id":"w-$bu","name":"$bu"}]""")
            }
            srv.json("GET", "/api/v1/runs", 200, "[]")
            var hidden = emptySet<String>()
            val store = newStore(srv, hidden = { hidden })

            val job = store.refresh()
            assertTrue("request never reached the (blocked) server", requestArrived.await(5, TimeUnit.SECONDS))
            hidden = setOf("apps") // the user hides "apps" while the first fetch is still in flight
            release.countDown()
            runBlocking { job.join() }

            assertEquals(listOf("infra"), store.visibleBus().map { it.slug })
            assertEquals(listOf("w-infra"), store.workspaces.map { it.id })
            assertNull(store.buState("apps"))
        }
    }

    // ─── carried over from the single-BU store ───────────────────────────────────────

    @Test
    fun `single in-flight refresh - two concurrent calls make exactly one BU list request`() {
        StubServer().use { srv ->
            var inflight = 0
            var max = 0
            srv.on("GET", "/api/v1/business-units") { _, ex ->
                synchronized(this) { inflight++; max = maxOf(max, inflight) }
                Thread.sleep(50)
                synchronized(this) { inflight-- }
                StubServer.respond(ex, 200, buJson("infra"))
            }
            srv.json("GET", "/api/v1/workspaces", 200, "[]")
            srv.json("GET", "/api/v1/runs", 200, "[]")
            val store = newStore(srv)

            val j1 = store.refresh()
            val j2 = store.refresh()
            runBlocking { j1.join(); j2.join() }

            assertEquals(1, max)
            assertEquals(1, srv.calls("GET", "/api/v1/business-units").size)
        }
    }

    @Test
    fun `stop during an in-flight tick is not undone by the tick's reschedule`() {
        StubServer().use { srv ->
            var reqs = 0
            srv.on("GET", "/api/v1/business-units") { _, ex ->
                synchronized(this) { reqs++ }
                Thread.sleep(150)
                StubServer.respond(ex, 200, "[]")
            }
            val store = newStore(srv)

            store.start(20)
            val deadline = System.currentTimeMillis() + 5_000
            while (synchronized(this) { reqs } == 0 && System.currentTimeMillis() < deadline) Thread.sleep(5)
            assertEquals(1, synchronized(this) { reqs })

            store.stop()
            Thread.sleep(200)
            val afterStop = synchronized(this) { reqs }
            Thread.sleep(200)
            assertEquals(afterStop, synchronized(this) { reqs })
            store.dispose()
        }
    }

    @Test
    fun `clears the snapshot and issues no request when the client provider returns null`() {
        StubServer().use { srv ->
            serveBus(srv, "infra")
            val store = Store(scope)
            var signedIn = true
            val c = client(srv.url)
            store.clientProvider = { if (signedIn) c else null }
            store.runsLimitProvider = { 50 }
            store.hiddenProvider = { emptySet() }

            store.refreshAndWait()
            assertEquals(1, store.workspaces.size)
            val before = srv.calls.size

            signedIn = false
            store.refreshAndWait()

            assertTrue(store.workspaces.isEmpty())
            assertTrue(store.runs.isEmpty())
            assertTrue(store.businessUnits.isEmpty())
            assertTrue(store.runsFor("w-infra").isEmpty())
            assertEquals(before, srv.calls.size)
        }
    }

    @Test
    fun `refresh sorts a BU's runs newest-first by created_at and indexes them by workspace`() {
        StubServer().use { srv ->
            srv.json("GET", "/api/v1/business-units", 200, buJson("infra"))
            srv.json("GET", "/api/v1/workspaces", 200, """[{"id":"w1","name":"a"}]""")
            srv.json(
                "GET", "/api/v1/runs", 200,
                """[{"id":"r1","workspace_id":"w1","command":"plan","status":"failed","created_at":"2026-01-01"},""" +
                    """{"id":"r2","workspace_id":"w1","command":"plan","status":"planned","created_at":"2026-01-02"}]""",
            )
            val store = newStore(srv)

            store.refreshAndWait()

            assertEquals(listOf("r2", "r1"), store.buState("infra")!!.runs.map { it.id })
            assertEquals(listOf("r2", "r1"), store.runsFor("w1").map { it.id })
        }
    }

    @Test
    fun `listeners fire once per change and are removed when their Disposable is disposed`() {
        StubServer().use { srv ->
            serveBus(srv, "infra")
            val store = newStore(srv)

            val parent = Disposer.newDisposable()
            val count = AtomicInteger()
            store.addListener(parent) { count.incrementAndGet() }

            store.refreshAndWait()
            assertEquals(1, count.get())

            store.refreshAndWait()
            assertEquals(2, count.get())

            Disposer.dispose(parent)
            store.refreshAndWait()
            assertEquals(2, count.get()) // no longer notified once its Disposable is disposed
        }
    }

    @Test
    fun `setActive false keeps the loop ticking with no requests, setActive true resumes them`() {
        StubServer().use { srv ->
            serveBus(srv, "infra")
            val store = newStore(srv)

            store.setActive(false)
            store.start(10)
            Thread.sleep(80)
            assertEquals(0, srv.calls.size)

            store.refreshAndWait() // a manual refresh is never gated by active
            assertEquals(3, srv.calls.size) // /business-units + one /workspaces + one /runs

            store.setActive(true)
            Thread.sleep(150)
            store.stop()
            assertTrue("expected the resumed loop to have issued more requests", srv.calls.size > 3)
        }
    }

    @Test
    fun `runsLimit is read live from the provider on every refresh`() {
        StubServer().use { srv ->
            serveBus(srv, "infra")
            val store = newStore(srv)
            var limit = 10
            store.runsLimitProvider = { limit }

            store.refreshAndWait()
            assertEquals("limit=10", srv.calls("GET", "/api/v1/runs").last().query)

            limit = 200
            store.refreshAndWait()
            assertEquals("limit=200", srv.calls("GET", "/api/v1/runs").last().query)
        }
    }

    @Test
    fun `calling start twice does not double-poll`() {
        StubServer().use { srv ->
            var reqs = 0
            srv.on("GET", "/api/v1/business-units") { _, ex ->
                synchronized(this) { reqs++ }
                StubServer.respond(ex, 200, "[]")
            }
            val store = newStore(srv)

            store.start(20)
            store.start(20) // must cancel the first loop rather than run a second one alongside it
            Thread.sleep(300)
            store.stop()

            val count = synchronized(this) { reqs }
            // A single 20ms-interval loop over ~300ms fires roughly 300/20 = 15 times; two
            // independent loops racing side by side would fire roughly twice that.
            assertTrue("expected a handful of requests from one loop, got $count", count in 2..24)
        }
    }

    @Test
    fun `visible BUs without data yet show up as unloaded`() {
        StubServer().use { srv ->
            val store = newStore(srv)
            store.setSnapshotForTest(listOf(BuState(com.terraducktel.jetbrains.api.BusinessUnit("1", "infra", "Infra"), loaded = false)))
            assertFalse(store.visibleStates().single().loaded)
            assertEquals(CopyOnWriteArrayList<String>().size, store.workspaces.size)
        }
    }

    // ─── the BU filter ───────────────────────────────────────────────────────────────

    @Test
    fun `applying a filter persists the hidden slugs, notifies, and refetches only what is still visible`() {
        StubServer().use { srv ->
            serveBus(srv, "infra", "apps", "data")
            var persisted = emptySet<String>()
            val store = newStore(srv, hidden = { persisted })
            store.hiddenWriter = { persisted = it }
            store.refreshAndWait()
            assertEquals(3, store.visibleBus().size)
            val notified = AtomicInteger()
            store.addListener(Disposer.newDisposable()) { notified.incrementAndGet() }
            val callsBefore = srv.calls("GET", "/api/v1/workspaces").size

            assertTrue(store.applyFilter(setOf("infra")))

            assertEquals(setOf("apps", "data"), persisted)
            assertEquals(listOf("infra"), store.visibleBus().map { it.slug })
            assertTrue("the tree must hear about the new filter at once", notified.get() >= 1)
            runBlocking { store.refresh().join() }
            val afterFilter = srv.calls("GET", "/api/v1/workspaces").drop(callsBefore)
            assertTrue(afterFilter.isNotEmpty())
            assertTrue(afterFilter.all { it.headers["x-business-unit"] == "infra" })
        }
    }

    @Test
    fun `applying a filter with no business unit selected is rejected and changes nothing`() {
        StubServer().use { srv ->
            serveBus(srv, "infra", "apps")
            var persisted = setOf("apps")
            val store = newStore(srv, hidden = { persisted })
            store.hiddenWriter = { persisted = it }
            store.refreshAndWait()
            val notified = AtomicInteger()
            store.addListener(Disposer.newDisposable()) { notified.incrementAndGet() }

            assertFalse(store.applyFilter(emptySet()))

            assertEquals(setOf("apps"), persisted)
            assertEquals(0, notified.get())
            assertEquals(listOf("infra"), store.visibleBus().map { it.slug })
        }
    }

    @Test
    fun `showing every business unit again clears the hidden set`() {
        StubServer().use { srv ->
            serveBus(srv, "infra", "apps")
            var persisted = setOf("apps")
            val store = newStore(srv, hidden = { persisted })
            store.hiddenWriter = { persisted = it }
            store.refreshAndWait()

            assertTrue(store.applyFilter(setOf("infra", "apps")))

            assertEquals(emptySet<String>(), persisted)
        }
    }
}
