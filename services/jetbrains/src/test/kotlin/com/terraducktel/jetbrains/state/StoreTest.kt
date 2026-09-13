package com.terraducktel.jetbrains.state

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
 * Plain-JUnit port of the [Store] behaviours in `services/vscode/test/unit/store.test.ts` that
 * Task 8 calls out: single-flight refresh, failure keeps the last snapshot, `stop()` during an
 * in-flight tick doesn't re-arm, and a client swap mid-fetch is discarded and retried. Constructs
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
}
