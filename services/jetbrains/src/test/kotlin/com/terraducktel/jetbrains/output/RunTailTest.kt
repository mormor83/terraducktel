package com.terraducktel.jetbrains.output

import com.terraducktel.jetbrains.api.TdtClient
import com.terraducktel.jetbrains.api.TokenProvider
import com.terraducktel.jetbrains.testutil.StubServer
import org.junit.After
import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/** Plain-JUnit port of `services/vscode/test/unit/runOutput.test.ts`'s `tailRun` cases, against
 *  [StubServer] instead of the TS `FakeServer`. */
class RunTailTest {

    private class FakeTokens : TokenProvider {
        override fun getAccessToken() = "t"
        override fun refreshAccessToken(): String? = "t"
        override fun hasCredential() = true
        override fun signOut() {}
    }

    /** Every timer created via [schedule] — shut down in [tearDown] so no test leaks a live thread
     *  into the next one (or into the JVM's shutdown, under a JUnit runner that reuses threads). */
    private val timers = CopyOnWriteArrayList<ScheduledExecutorService>()

    private fun schedule(delayMs: Long, block: () -> Unit) {
        val exec = Executors.newSingleThreadScheduledExecutor()
        timers += exec
        exec.schedule(block, delayMs, TimeUnit.MILLISECONDS)
    }

    @After fun tearDown() {
        timers.forEach { it.shutdownNow() }
    }

    private fun sinceOf(query: String?): Int =
        (query ?: "").split("&").firstOrNull { it.startsWith("since=") }?.substringAfter("=")?.toIntOrNull() ?: 0

    // 1. Cursor stays on the first unfinished step; headers printed once; finished output not
    //    repeated; terminal flush prints "── run planned".
    @Test fun `appends only new steps using the since cursor and stops at a terminal status`() = StubServer().use { srv ->
        val poll = AtomicInteger(0)
        srv.on("GET", "/api/v1/runs/r1") { _, ex ->
            val p = poll.incrementAndGet()
            StubServer.respond(ex, 200, """{"id":"r1","workspace_id":"w","command":"plan","status":"${if (p < 3) "running" else "planned"}"}""")
        }
        srv.on("GET", "/api/v1/runs/r1/steps") { call, ex ->
            val since = sinceOf(call.query)
            val p = poll.get()
            val planStatus = if (p < 3) "running" else "success"
            val planOutput = if (p < 3) "planning…" else "planning…\\nNo changes."
            val all = listOf(
                """{"position":0,"name":"Init","status":"success","output":"ok\n"}""",
                """{"position":1,"name":"Plan","status":"$planStatus","output":"$planOutput"}""",
            )
            val filtered = all.filterIndexed { i, _ -> i >= since }
            StubServer.respond(ex, 200, filtered.joinToString(",", "[", "]"))
        }
        val client = TdtClient(srv.url, "b", FakeTokens())
        val lines = CopyOnWriteArrayList<String>()

        val final = RunTail.tail(client, "r1", LineSink { lines.add(it) }, pollMs = 5)

        assertEquals("planned", final.status)
        assertEquals(1, lines.count { it.contains("── Init") }) // header printed once
        assertTrue(lines.joinToString("\n"), lines.joinToString("\n").contains("No changes."))
        assertEquals(1, lines.count { it == "ok" }) // finished step output not repeated
        val sinces = srv.calls("GET", "/api/v1/runs/r1/steps").map { sinceOf(it.query) }
        assertEquals(0, sinces.first())
        assertTrue(sinces.drop(1).all { it == 1 }) // cursor stays on the unfinished step
    }

    // 2. Cancellation returns the last observed run.
    @Test fun `honours cancellation`() = StubServer().use { srv ->
        srv.json("GET", "/api/v1/runs/r1", 200, """{"id":"r1","workspace_id":"w","command":"plan","status":"running"}""")
        srv.json("GET", "/api/v1/runs/r1/steps", 200, "[]")
        val client = TdtClient(srv.url, "b", FakeTokens())
        val cancelled = AtomicInteger(0)
        schedule(30) { cancelled.set(1) }

        val final = RunTail.tail(client, "r1", LineSink {}, pollMs = 5, isCancelled = { cancelled.get() == 1 })

        assertEquals("running", final.status)
    }

    // 3. Nothing appended after the cancel flag flips.
    @Test fun `appends nothing after the cancel flag flips`() = StubServer().use { srv ->
        srv.json("GET", "/api/v1/runs/r1", 200, """{"id":"r1","workspace_id":"w","command":"plan","status":"running"}""")
        val poll = AtomicInteger(0)
        srv.on("GET", "/api/v1/runs/r1/steps") { _, ex ->
            val p = poll.incrementAndGet()
            // Output keeps growing, so every poll WOULD append a line if the loop kept going.
            val output = (0 until p).joinToString("\\n") { "line $it" } + "\\n"
            StubServer.respond(ex, 200, """[{"position":0,"name":"Plan","status":"running","output":"$output"}]""")
        }
        val client = TdtClient(srv.url, "b", FakeTokens())
        val lines = CopyOnWriteArrayList<String>()
        val cancelled = AtomicInteger(0)
        val lenAtCancel = AtomicInteger(-1)
        // The timer thread and the tail loop are two independent JVM threads, so this is not truly
        // synchronous with `applySteps` — but the window between the timer's check and it actually
        // firing is microseconds, against a poll/sleep cycle of ~10ms (5ms request + 5ms sleep), so
        // in practice the recorded length matches what was printed at the moment of cancellation
        // (asserted below, rather than assumed).
        schedule(30) { cancelled.set(1); lenAtCancel.set(lines.size) }

        RunTail.tail(client, "r1", LineSink { lines.add(it) }, pollMs = 5, isCancelled = { cancelled.get() == 1 })

        assertTrue(lenAtCancel.get() > 0)
        assertEquals(lenAtCancel.get(), lines.size)
    }

    // 4. Stops polling once cancelled, even when the run never lands.
    @Test fun `stops polling once cancelled, even when the run never lands`() = StubServer().use { srv ->
        srv.json("GET", "/api/v1/runs/r1", 200, """{"id":"r1","workspace_id":"w","command":"plan","status":"running"}""")
        srv.json("GET", "/api/v1/runs/r1/steps", 200, "[]")
        val client = TdtClient(srv.url, "b", FakeTokens())
        val cancelled = AtomicInteger(0)
        schedule(30) { cancelled.set(1) }

        val pool = Executors.newSingleThreadExecutor()
        val future = pool.submit<Unit> { RunTail.tail(client, "r1", LineSink {}, pollMs = 5, isCancelled = { cancelled.get() == 1 }); Unit }
        Thread.sleep(60) // well past the 30ms cancellation
        fun runGets() = srv.calls("GET", "/api/v1/runs/r1").size
        val countAfterCancel = runGets()
        Thread.sleep(60) // no further polling should happen
        assertEquals(countAfterCancel, runGets())
        future.get(10, TimeUnit.SECONDS)
        pool.shutdown()
    }
}
