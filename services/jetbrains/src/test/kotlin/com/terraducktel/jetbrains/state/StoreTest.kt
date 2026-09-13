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
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

private class FakeTokens : TokenProvider {
    override fun getAccessToken() = "t"
    override fun refreshAccessToken(): String? = "t"
    override fun hasCredential() = true
    override fun signOut() {}
}

/**
 * Plain-JUnit port of the [Store] behaviours in `services/vscode/test/unit/store.test.ts`:
 * single-flight refresh, failure keeps the last snapshot, `stop()` during an in-flight tick
 * doesn't re-arm (nor does a second `start()` double-poll alongside the first), a client swap
 * mid-fetch is discarded and retried, runs are sorted newest-first and indexed by workspace,
 * listeners fire once per change and are removed with their `Disposable`, `setActive()` gates the
 * network without stopping the tick, and `runsLimit` is read live on every refresh. Constructs
 * [Store] directly (no platform) and drives it against a real [TdtClient] + [StubServer], using
 * the `clientProvider`/`runsLimitProvider` test seams instead of [com.terraducktel.jetbrains.
 * session.TdtSession] / [com.terraducktel.jetbrains.settings.TdtSettings].
 */
class StoreTest {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    @After
    fun tearDown() {
        scope.cancel()
    }

    private fun client(url: String) = TdtClient(url, "default", FakeTokens())

    @Test
    fun `single in-flight refresh - two concurrent calls make exactly one workspaces request`() {
        StubServer().use { srv ->
            var inflight = 0
            var max = 0
            srv.on("GET", "/api/v1/workspaces") { _, ex ->
                synchronized(this) { inflight++; max = maxOf(max, inflight) }
                Thread.sleep(50)
                synchronized(this) { inflight-- }
                StubServer.respond(ex, 200, "[]")
            }
            srv.json("GET", "/api/v1/runs", 200, "[]")
            val store = Store(scope)
            val c = client(srv.url)
            store.clientProvider = { c }
            store.runsLimitProvider = { 50 }

            val j1 = store.refresh()
            val j2 = store.refresh()
            runBlocking { j1.join(); j2.join() }

            assertEquals(1, max)
            assertEquals(1, srv.calls("GET", "/api/v1/workspaces").size)
        }
    }

    @Test
    fun `a failed refresh keeps the last good snapshot and bumps consecutiveFailures`() {
        StubServer().use { good ->
            good.json("GET", "/api/v1/workspaces", 200, """[{"id":"w1","name":"a"}]""")
            good.json("GET", "/api/v1/runs", 200, "[]")
            val dead = StubServer()
            dead.close() // closed before first use: every request to it fails immediately

            val store = Store(scope)
            var useGood = true
            val goodClient = client(good.url)
            val deadClient = client(dead.url)
            store.clientProvider = { if (useGood) goodClient else deadClient }
            store.runsLimitProvider = { 50 }

            store.refreshAndWait()
            assertEquals(listOf("w1"), store.workspaces.map { it.id })
            assertEquals(0, store.consecutiveFailures)

            useGood = false
            store.refreshAndWait()

            assertEquals(listOf("w1"), store.workspaces.map { it.id }) // last good snapshot kept
            assertEquals(1, store.consecutiveFailures)
            assertTrue(store.lastError != null)
        }
    }

    @Test
    fun `a client swap mid-fetch discards the stale response and retries with the new client`() {
        StubServer().use { srvA ->
            StubServer().use { srvB ->
                val requestArrived = CountDownLatch(1)
                val release = CountDownLatch(1)
                srvA.on("GET", "/api/v1/workspaces") { _, ex ->
                    requestArrived.countDown()
                    assertTrue("release latch was never opened", release.await(5, TimeUnit.SECONDS))
                    StubServer.respond(ex, 200, """[{"id":"a","name":"a"}]""")
                }
                srvA.json("GET", "/api/v1/runs", 200, "[]")
                srvB.json("GET", "/api/v1/workspaces", 200, """[{"id":"b","name":"b"}]""")
                srvB.json("GET", "/api/v1/runs", 200, "[]")

                val clientA = client(srvA.url)
                val clientB = client(srvB.url)
                var current: TdtClient = clientA
                val store = Store(scope)
                store.clientProvider = { current }
                store.runsLimitProvider = { 50 }

                val job = store.refresh()
                assertTrue("request never reached the (blocked) server", requestArrived.await(5, TimeUnit.SECONDS))
                current = clientB // swap while A's request is still in flight
                release.countDown()
                runBlocking { job.join() }

                assertEquals(listOf("b"), store.workspaces.map { it.id })
            }
        }
    }

    @Test
    fun `stop during an in-flight tick is not undone by the tick's reschedule`() {
        StubServer().use { srv ->
            var reqs = 0
            srv.on("GET", "/api/v1/workspaces") { _, ex ->
                synchronized(this) { reqs++ }
                Thread.sleep(150)
                StubServer.respond(ex, 200, "[]")
            }
            srv.json("GET", "/api/v1/runs", 200, "[]")
            val store = Store(scope)
            val c = client(srv.url) // one stable instance: a fresh client per call would make
            store.clientProvider = { c } // doRefresh()'s stale-client check always trip and retry
            store.runsLimitProvider = { 50 }

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
            srv.json("GET", "/api/v1/workspaces", 200, """[{"id":"w1","name":"a"}]""")
            srv.json(
                "GET", "/api/v1/runs", 200,
                """[{"id":"r1","workspace_id":"w1","command":"plan","status":"planned"}]""",
            )
            val store = Store(scope)
            var signedIn = true
            val c = client(srv.url)
            store.clientProvider = { if (signedIn) c else null }
            store.runsLimitProvider = { 50 }

            store.refreshAndWait()
            assertEquals(1, store.workspaces.size)
            val before = srv.calls.size

            signedIn = false
            store.refreshAndWait()

            assertTrue(store.workspaces.isEmpty())
            assertTrue(store.runs.isEmpty())
            assertTrue(store.runsFor("w1").isEmpty())
            assertEquals(before, srv.calls.size)
        }
    }

    @Test
    fun `refresh sorts runs newest-first by created_at and indexes them by workspace`() {
        StubServer().use { srv ->
            srv.json("GET", "/api/v1/workspaces", 200, """[{"id":"w1","name":"a"}]""")
            srv.json(
                "GET", "/api/v1/runs", 200,
                """[{"id":"r2","workspace_id":"w1","command":"plan","status":"planned","created_at":"2026-01-02"},""" +
                    """{"id":"r1","workspace_id":"w1","command":"plan","status":"failed","created_at":"2026-01-01"}]""",
            )
            val store = Store(scope)
            val c = client(srv.url) // one stable instance: a fresh client per call would trip
            store.clientProvider = { c } // doRefresh()'s stale-client check and force needless retries
            store.runsLimitProvider = { 50 }

            store.refreshAndWait()

            assertEquals(listOf("w1"), store.workspaces.map { it.id })
            assertEquals(listOf("r2", "r1"), store.runs.map { it.id })
            assertEquals(listOf("r2", "r1"), store.runsFor("w1").map { it.id })
        }
    }

    @Test
    fun `listeners fire once per change and are removed when their Disposable is disposed`() {
        StubServer().use { srv ->
            srv.json("GET", "/api/v1/workspaces", 200, "[]")
            srv.json("GET", "/api/v1/runs", 200, "[]")
            val store = Store(scope)
            val c = client(srv.url) // one stable instance: a fresh client per call would trip
            store.clientProvider = { c } // doRefresh()'s stale-client check and force needless retries
            store.runsLimitProvider = { 50 }

            val parent = Disposer.newDisposable()
            var count = 0
            store.addListener(parent) { count++ }

            store.refreshAndWait()
            assertEquals(1, count)

            store.refreshAndWait()
            assertEquals(2, count)

            Disposer.dispose(parent)
            store.refreshAndWait()
            assertEquals(2, count) // no longer notified once its Disposable is disposed
        }
    }

    @Test
    fun `setActive false keeps the loop ticking with no requests, setActive true resumes them`() {
        StubServer().use { srv ->
            srv.json("GET", "/api/v1/workspaces", 200, "[]")
            srv.json("GET", "/api/v1/runs", 200, "[]")
            val store = Store(scope)
            val c = client(srv.url) // one stable instance: a fresh client per call would trip
            store.clientProvider = { c } // doRefresh()'s stale-client check and force needless retries
            store.runsLimitProvider = { 50 }

            store.setActive(false)
            store.start(10)
            Thread.sleep(80)
            assertEquals(0, srv.calls.size)

            store.refreshAndWait() // a manual refresh is never gated by active
            assertEquals(2, srv.calls.size) // one /workspaces + one /runs call

            store.setActive(true)
            Thread.sleep(150)
            store.stop()
            assertTrue("expected the resumed loop to have issued more requests", srv.calls.size > 2)
        }
    }

    @Test
    fun `runsLimit is read live from the provider on every refresh`() {
        StubServer().use { srv ->
            srv.json("GET", "/api/v1/workspaces", 200, "[]")
            srv.json("GET", "/api/v1/runs", 200, "[]")
            val store = Store(scope)
            val c = client(srv.url) // stable instance — see the note on the other tests above
            store.clientProvider = { c }
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
            srv.on("GET", "/api/v1/workspaces") { _, ex ->
                synchronized(this) { reqs++ }
                StubServer.respond(ex, 200, "[]")
            }
            srv.json("GET", "/api/v1/runs", 200, "[]")
            val store = Store(scope)
            val c = client(srv.url) // one stable instance: see the other stop()/start() test
            store.clientProvider = { c }
            store.runsLimitProvider = { 50 }

            store.start(20)
            store.start(20) // must cancel the first loop rather than run a second one alongside it
            Thread.sleep(300)
            store.stop()

            val count = synchronized(this) { reqs }
            // A single 20ms-interval loop over ~300ms fires roughly 300/20 = 15 times (each
            // request/response here is effectively instant, plus/minus scheduling jitter); two
            // independent loops racing side by side would fire roughly twice that. Assert well
            // under 2x while still requiring the loop to have actually ticked more than once.
            assertTrue("expected a handful of requests from one loop, got $count", count in 2..24)
        }
    }
}
