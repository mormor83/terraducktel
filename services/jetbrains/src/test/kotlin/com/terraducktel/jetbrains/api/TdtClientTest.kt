package com.terraducktel.jetbrains.api

import com.terraducktel.jetbrains.testutil.StubServer
import org.junit.Assert.*
import org.junit.Test
import java.io.IOException
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/** Port of `services/vscode/test/unit/client.test.ts` against [StubServer]. */
private class FakeTokens(var token: String? = "t", var cred: Boolean = true, val onRefresh: () -> String? = { "t2" }) : TokenProvider {
    val refreshes = AtomicInteger(); val signOuts = AtomicInteger()
    override fun getAccessToken() = token
    override fun refreshAccessToken(): String? { refreshes.incrementAndGet(); return onRefresh().also { token = it } }
    override fun hasCredential() = cred
    override fun signOut() { signOuts.incrementAndGet(); token = null; cred = false }
}

class TdtClientTest {
    /** Runs [block] and returns the [ApiError] it must throw; fails the test otherwise. Used
     *  instead of `runCatching` so every concurrent call's outcome is individually asserted,
     *  rather than collected and left unchecked. */
    private fun captureApiError(block: () -> Any?): ApiError {
        try { block() } catch (e: ApiError) { return e }
        throw AssertionError("expected an ApiError to be thrown")
    }

    // 1. Auth headers on data calls, absent on public auth endpoints.
    @Test fun `sends bearer and BU headers on data calls, not on public auth endpoints`() = StubServer().use { srv ->
        srv.json("GET", "/api/v1/workspaces", 200, "[]")
        srv.json("GET", "/api/v1/auth/config", 200, """{"mode":"local","oidc_enabled":false}""")
        srv.json("POST", "/api/v1/auth/token", 200, """{"access_token":"a","refresh_token":"r"}""")
        val c = TdtClient(srv.url, "default", FakeTokens())

        c.listWorkspaces()
        val dataCall = srv.calls("GET", "/api/v1/workspaces").single()
        assertEquals("Bearer t", dataCall.headers["authorization"])
        assertEquals("default", dataCall.headers["x-business-unit"])

        c.authConfig()
        val authCall = srv.calls("GET", "/api/v1/auth/config").single()
        assertNull(authCall.headers["authorization"])
        assertNull(authCall.headers["x-business-unit"])

        c.login("a@b.com", "x")
        val loginCall = srv.calls("POST", "/api/v1/auth/token").single()
        assertNull(loginCall.headers["authorization"])
        assertNull(loginCall.headers["x-business-unit"])
    }

    // 2. Query encoding.
    @Test fun `encodes list and scalar query params`() = StubServer().use { srv ->
        srv.json("GET", "/api/v1/runs", 200, "[]")
        srv.json("GET", "/api/v1/runs/r1/steps", 200, "[]")
        val c = TdtClient(srv.url, "default", FakeTokens())

        c.listRuns(limit = 5, status = listOf("awaiting_approval", "planned"))
        assertEquals("limit=5&status=awaiting_approval%2Cplanned", srv.calls("GET", "/api/v1/runs").single().query)

        c.getSteps("r1", since = 3, includeOutput = false)
        assertEquals("since=3&include_output=false", srv.calls("GET", "/api/v1/runs/r1/steps").single().query)
    }

    @Test fun `getSteps with defaults omits include_output`() = StubServer().use { srv ->
        srv.json("GET", "/api/v1/runs/r1/steps", 200, "[]")
        val c = TdtClient(srv.url, "default", FakeTokens())
        c.getSteps("r1")
        val query = srv.calls("GET", "/api/v1/runs/r1/steps").single().query
        assertFalse(query?.contains("include_output") ?: false)
    }

    // 6. Restored: a query value containing a space is percent-encoded as %20, not '+'.
    @Test fun `encodes a space in a query value as percent-20`() = StubServer().use { srv ->
        srv.json("GET", "/api/v1/runs", 200, "[]")
        val c = TdtClient(srv.url, "default", FakeTokens())
        c.listRuns(workspaceId = "w 1")
        val query = srv.calls("GET", "/api/v1/runs").single().query ?: ""
        assertTrue(query, query.contains("workspace_id=w%201"))
    }

    // 3. 401 -> refresh once -> retry once -> success.
    @Test fun `401 triggers exactly one refresh then retries with the fresh token`() = StubServer().use { srv ->
        srv.on("GET", "/api/v1/workspaces") { call, ex ->
            if (call.headers["authorization"] == "Bearer t") StubServer.respond(ex, 401, """{"detail":"expired"}""")
            else StubServer.respond(ex, 200, "[]")
        }
        val tp = FakeTokens()
        val c = TdtClient(srv.url, "default", tp)

        assertEquals(emptyList<Any>(), c.listWorkspaces())
        assertEquals(1, tp.refreshes.get())
        val calls = srv.calls("GET", "/api/v1/workspaces")
        assertEquals(2, calls.size)
        assertEquals("Bearer t2", calls[1].headers["authorization"])
    }

    // 4. 401 whose token was already rotated elsewhere -> retries with it, no refresh call.
    @Test fun `401 with an externally rotated token retries without calling refresh`() = StubServer().use { srv ->
        val tp = FakeTokens()
        srv.on("GET", "/api/v1/workspaces") { call, ex ->
            if (call.headers["authorization"] == "Bearer t") {
                tp.token = "rotated" // simulate another caller rotating the token, not via refreshAccessToken()
                StubServer.respond(ex, 401, """{"detail":"expired"}""")
            } else StubServer.respond(ex, 200, "[]")
        }
        val c = TdtClient(srv.url, "default", tp)

        assertEquals(emptyList<Any>(), c.listWorkspaces())
        assertEquals(0, tp.refreshes.get())
        val calls = srv.calls("GET", "/api/v1/workspaces")
        assertEquals(2, calls.size)
        assertEquals("Bearer rotated", calls[1].headers["authorization"])
    }

    // 5. Second 401 after refresh -> sign-out once, listeners fired once, ApiError(401) thrown.
    @Test fun `a second 401 after refresh signs out once and throws`() = StubServer().use { srv ->
        srv.json("GET", "/api/v1/workspaces", 401, """{"detail":"nope"}""")
        val tp = FakeTokens()
        val c = TdtClient(srv.url, "default", tp)
        var fired = 0
        c.onSignedOut { fired++ }

        val err = try { c.listWorkspaces(); null } catch (e: ApiError) { e }
        assertNotNull(err)
        assertEquals(401, err!!.status)
        assertEquals(1, tp.refreshes.get())
        assertEquals(1, tp.signOuts.get())
        assertEquals(1, fired)
    }

    // Additional: refreshAccessToken() returning null (definitively dead credential) -> retry
    // skipped (no second attempt made), one sign-out, ApiError(401).
    @Test fun `refreshAccessToken returning null skips the retry and signs out once`() = StubServer().use { srv ->
        srv.json("GET", "/api/v1/workspaces", 401, """{"detail":"expired"}""")
        val tp = FakeTokens(onRefresh = { null })
        val c = TdtClient(srv.url, "default", tp)
        var fired = 0
        c.onSignedOut { fired++ }

        val err = try { c.listWorkspaces(); null } catch (e: ApiError) { e }
        assertNotNull(err)
        assertEquals(401, err!!.status)
        assertEquals(1, tp.refreshes.get())
        assertEquals(1, tp.signOuts.get())
        assertEquals(1, fired)
        assertEquals(1, srv.calls("GET", "/api/v1/workspaces").size) // no second attempt
    }

    // 6. Concurrent terminal 401s from 4 threads, forced to genuinely overlap via a latch, spread
    //    across a client and its withBu() clone -> exactly one sign-out and one listener fire.
    @Test fun `concurrent terminal 401s across a client and its withBu clone coalesce to one sign-out`() = StubServer().use { srv ->
        val arrived = CountDownLatch(4)
        srv.on("GET", "/api/v1/workspaces") { _, ex ->
            arrived.countDown()
            arrived.await(5, TimeUnit.SECONDS) // hold all 4 requests in flight together
            StubServer.respond(ex, 401, """{"detail":"nope"}""")
        }
        val tp = FakeTokens()
        val c = TdtClient(srv.url, "default", tp)
        val clone = c.withBu("other")
        var fired = 0
        c.onSignedOut { fired++ }

        val pool = Executors.newFixedThreadPool(4)
        val errors: List<ApiError>
        try {
            val futures = listOf(
                pool.submit<ApiError> { captureApiError { c.listWorkspaces() } },
                pool.submit<ApiError> { captureApiError { clone.listWorkspaces() } },
                pool.submit<ApiError> { captureApiError { c.listWorkspaces() } },
                pool.submit<ApiError> { captureApiError { clone.listWorkspaces() } },
            )
            errors = futures.map { it.get(10, TimeUnit.SECONDS) }
        } finally { pool.shutdown() }

        assertEquals(4, errors.size)
        errors.forEach { assertEquals(401, it.status) }
        assertEquals(1, tp.refreshes.get())
        assertEquals(1, tp.signOuts.get())
        assertEquals(1, fired)
    }

    // Port of client.test.ts "serialises concurrent refreshes (one refresh for N parallel 401s)".
    @Test fun `serialises concurrent refreshes into exactly one refresh`() = StubServer().use { srv ->
        srv.on("GET", "/api/v1/workspaces") { call, ex ->
            if (call.headers["authorization"] == "Bearer t") StubServer.respond(ex, 401, "{}")
            else StubServer.respond(ex, 200, "[]")
        }
        val tp = FakeTokens()
        val c = TdtClient(srv.url, "default", tp)
        val pool = Executors.newFixedThreadPool(3)
        try {
            val futures = (1..3).map { pool.submit<List<Workspace>> { c.listWorkspaces() } }
            futures.forEach { assertEquals(emptyList<Workspace>(), it.get(10, TimeUnit.SECONDS)) }
        } finally { pool.shutdown() }
        assertEquals(1, tp.refreshes.get())
    }

    // Port of client.test.ts "with slow refresh, concurrent 401s still make exactly one refresh
    // and all succeed" (widened to 4 threads per the fix-round-1 instructions).
    @Test fun `slow refresh across 4 concurrent 401s still makes exactly one refresh, all succeed`() = StubServer().use { srv ->
        srv.on("GET", "/api/v1/workspaces") { call, ex ->
            if (call.headers["authorization"] == "Bearer t") StubServer.respond(ex, 401, "{}")
            else StubServer.respond(ex, 200, "[]")
        }
        val tp = FakeTokens(onRefresh = { Thread.sleep(30); "t2" })
        val c = TdtClient(srv.url, "default", tp)
        val pool = Executors.newFixedThreadPool(4)
        try {
            val futures = (1..4).map { pool.submit<List<Workspace>> { c.listWorkspaces() } }
            futures.forEach { assertEquals(emptyList<Workspace>(), it.get(10, TimeUnit.SECONDS)) }
        } finally { pool.shutdown() }
        assertEquals(1, tp.refreshes.get())
    }

    // Port of client.test.ts "withBu clones share one in-flight refresh across a parent+clone burst".
    @Test fun `withBu clones share one in-flight refresh across a parent+clone burst`() = StubServer().use { srv ->
        srv.on("GET", "/api/v1/workspaces") { call, ex ->
            if (call.headers["authorization"] == "Bearer t") StubServer.respond(ex, 401, "{}")
            else StubServer.respond(ex, 200, "[]")
        }
        val tp = FakeTokens(onRefresh = { Thread.sleep(30); "t2" })
        val c = TdtClient(srv.url, "default", tp)
        val clone = c.withBu("other")
        val pool = Executors.newFixedThreadPool(4)
        try {
            val futures = listOf(
                pool.submit<List<Workspace>> { c.listWorkspaces() },
                pool.submit<List<Workspace>> { clone.listWorkspaces() },
                pool.submit<List<Workspace>> { c.listWorkspaces() },
                pool.submit<List<Workspace>> { clone.listWorkspaces() },
            )
            futures.forEach { assertEquals(emptyList<Workspace>(), it.get(10, TimeUnit.SECONDS)) }
        } finally { pool.shutdown() }
        assertEquals(1, tp.refreshes.get())
    }

    // Port of client.test.ts "sign-out can happen again after re-sign-in on the same client".
    @Test fun `sign-out can happen again after re-sign-in on the same client`() = StubServer().use { srv ->
        srv.json("GET", "/api/v1/workspaces", 401, """{"detail":"always unauthorized"}""")
        val tp = FakeTokens()
        val c = TdtClient(srv.url, "default", tp)
        var fired = 0
        c.onSignedOut { fired++ }

        val err1 = try { c.listWorkspaces(); null } catch (e: ApiError) { e }
        assertNotNull(err1)
        assertEquals(1, tp.signOuts.get())
        assertEquals(1, fired)

        // Simulate re-sign-in on the same client/AuthState.
        tp.token = "t3"
        tp.cred = true

        val err2 = try { c.listWorkspaces(); null } catch (e: ApiError) { e }
        assertNotNull(err2)
        assertEquals(2, tp.signOuts.get())
        assertEquals(2, fired)
    }

    // Port of client.test.ts "a later sign-out still fires while an unrelated request is in flight".
    @Test fun `a later sign-out still fires while an unrelated long request is in flight`() = StubServer().use { srv ->
        val releaseSlow = CountDownLatch(1)
        srv.json("GET", "/api/v1/workspaces", 401, """{"detail":"always unauthorized"}""")
        srv.on("GET", "/api/v1/runs") { _, ex ->
            releaseSlow.await(5, TimeUnit.SECONDS)
            StubServer.respond(ex, 200, "[]")
        }
        val tp = FakeTokens()
        val c = TdtClient(srv.url, "default", tp)
        var fired = 0
        c.onSignedOut { fired++ }

        val pool = Executors.newFixedThreadPool(1)
        val slow = pool.submit<List<Run>> { c.listRuns() }
        // Let the slow request capture the pre-sign-out epoch and reach the (blocked) handler
        // before the sign-out burst below runs, without relying on a fixed sleep for correctness
        // of the assertions that follow (only for good test hygiene / not racing the submit).
        Thread.sleep(100)

        val err = try { c.listWorkspaces(); null } catch (e: ApiError) { e }
        assertNotNull(err)
        assertEquals(1, tp.signOuts.get())
        assertEquals(1, fired)

        releaseSlow.countDown()
        try { assertEquals(emptyList<Run>(), slow.get(10, TimeUnit.SECONDS)) } finally { pool.shutdown() }
    }

    // 7. No access token but a stored credential -> sign out once, "Session expired", no HTTP request.
    @Test fun `no access token but a stored credential signs out with session expired`() = StubServer().use { srv ->
        srv.json("GET", "/api/v1/workspaces", 200, "[]")
        val tp = FakeTokens(token = null, cred = true)
        val c = TdtClient(srv.url, "default", tp)
        var fired = 0
        c.onSignedOut { fired++ }

        val err = try { c.listWorkspaces(); null } catch (e: ApiError) { e }
        assertNotNull(err)
        assertEquals(401, err!!.status)
        assertEquals("Session expired — sign in again", err.message)
        assertEquals(1, tp.signOuts.get())
        assertEquals(1, fired)
        assertEquals(0, srv.calls("GET", "/api/v1/workspaces").size)
    }

    // 8. No access token and no stored credential -> "Not signed in", no HTTP request, no sign-out.
    @Test fun `no access token and no credential fails closed without touching the network`() = StubServer().use { srv ->
        srv.json("GET", "/api/v1/workspaces", 200, "[]")
        val tp = FakeTokens(token = null, cred = false)
        val c = TdtClient(srv.url, "default", tp)
        var fired = 0
        c.onSignedOut { fired++ }

        val err = try { c.listWorkspaces(); null } catch (e: ApiError) { e }
        assertNotNull(err)
        assertEquals(401, err!!.status)
        assertEquals("Not signed in", err.message)
        assertEquals(0, tp.signOuts.get())
        assertEquals(0, fired)
        assertEquals(0, srv.calls("GET", "/api/v1/workspaces").size)
    }

    // 9. refreshAccessToken() throwing IOException -> original call throws it; no sign-out.
    @Test fun `a transient refresh failure propagates and never signs out`() = StubServer().use { srv ->
        srv.json("GET", "/api/v1/workspaces", 401, """{"detail":"expired"}""")
        val signOuts = AtomicInteger()
        val tp = object : TokenProvider {
            override fun getAccessToken() = "t"
            override fun refreshAccessToken(): String? = throw IOException("network down")
            override fun hasCredential() = true
            override fun signOut() { signOuts.incrementAndGet() }
        }
        val c = TdtClient(srv.url, "default", tp)
        var fired = 0
        c.onSignedOut { fired++ }

        val err = try { c.listWorkspaces(); null } catch (e: IOException) { e }
        assertNotNull(err)
        assertEquals("network down", err!!.message)
        assertEquals(0, signOuts.get())
        assertEquals(0, fired)
    }

    // 10. Error detail propagation, end to end through TdtClient.
    @Test fun `propagates detail messages from error bodies`() = StubServer().use { srv ->
        val c = TdtClient(srv.url, "default", FakeTokens())

        srv.json("POST", "/api/v1/workspaces/w1/runs", 409, """{"detail":"workspace is locked"}""")
        val e1 = try { c.triggerRun("w1", TriggerRunBody("plan")); null } catch (e: ApiError) { e }
        assertEquals("workspace is locked", e1?.message)

        srv.json("POST", "/api/v1/auth/token", 422, """{"detail":[{"loc":["body","command"],"msg":"field required"}]}""")
        val e2 = try { c.login("a@b.com", "x"); null } catch (e: ApiError) { e }
        assertEquals("command: field required", e2?.message)

        srv.json("GET", "/api/v1/runs/r1", 400, "oops")
        val e3 = try { c.getRun("r1"); null } catch (e: ApiError) { e }
        assertEquals("oops", e3?.message)

        srv.json("GET", "/api/v1/runs/r2", 500, "")
        val e4 = try { c.getRun("r2"); null } catch (e: ApiError) { e }
        assertEquals("HTTP 500", e4?.message)
    }

    // 11. trace receives "METHOD /path → status (N ms)" lines.
    @Test fun `trace receives method, path, status and duration`() = StubServer().use { srv ->
        srv.json("GET", "/api/v1/workspaces", 200, "[]")
        val lines = CopyOnWriteArrayList<String>()
        val c = TdtClient(srv.url, "default", FakeTokens(), trace = { lines.add(it) })

        c.listWorkspaces()
        assertEquals(1, lines.size)
        assertTrue(lines[0], Regex("""^GET /workspaces → 200 \(\d+ ms\)$""").matches(lines[0]))
    }
}
