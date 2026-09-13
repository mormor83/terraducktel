package com.terraducktel.jetbrains.auth

import org.junit.Assert.*
import org.junit.Test
import java.net.HttpURLConnection
import java.net.ServerSocket
import java.net.URI
import java.nio.charset.StandardCharsets.UTF_8
import java.util.concurrent.CompletableFuture
import java.util.concurrent.TimeUnit

/** Port of `services/vscode/test/unit/sso.test.ts`. */
class SsoTest {

    /** Captured `buildUrl(port, nonce)` inputs — mirrors the TS suite's `captured`. */
    private data class Captured(val port: Int, val nonce: String)

    /** GETs `url` on this thread; returns the HTTP status code. */
    private fun hit(url: String): Int {
        val conn = URI(url).toURL().openConnection() as HttpURLConnection
        conn.connectTimeout = 5000
        conn.readTimeout = 5000
        return try {
            conn.responseCode
        } finally {
            conn.disconnect()
        }
    }

    /** Reads the response body for a 2xx (throws for non-2xx, which callers don't need here). */
    private fun hitBody(url: String): Pair<Int, String> {
        val conn = URI(url).toURL().openConnection() as HttpURLConnection
        conn.connectTimeout = 5000
        conn.readTimeout = 5000
        val code = conn.responseCode
        val stream = if (code in 200..299) conn.inputStream else conn.errorStream
        val body = stream?.bufferedReader(UTF_8)?.readText() ?: ""
        conn.disconnect()
        return code to body
    }

    /** True once nothing is listening on `port` any more — i.e. the loopback server really closed. */
    private fun portReleased(port: Int): Boolean {
        repeat(30) {
            try {
                ServerSocket(port).close()
                return true
            } catch (_: Exception) {
                Thread.sleep(10)
            }
        }
        return false
    }

    // (a) success: openUrl captures the URL, a GET to /callback with matching nonce resolves the
    // pair and the response body mentions "close this tab".
    @Test fun `resolves with the token pair when the callback carries the right nonce`() {
        val captured = CompletableFuture<Captured>()
        val result = CompletableFuture.supplyAsync {
            Sso.runLoopbackLogin(
                buildUrl = { port, nonce -> captured.complete(Captured(port, nonce)); "http://x/login?cli_port=$port&cli_nonce=$nonce" },
                openUrl = { true },
                timeoutMs = 5000,
            )
        }
        val c = captured.get(5, TimeUnit.SECONDS)
        val (status, body) = hitBody("http://127.0.0.1:${c.port}/callback?access_token=A&refresh_token=R&nonce=${c.nonce}")

        assertEquals(200, status)
        assertTrue(body.contains("close this tab"))
        val pair = result.get(5, TimeUnit.SECONDS)
        assertEquals("A", pair.access_token)
        assertEquals("R", pair.refresh_token)
    }

    // (b) nonce mismatch -> 400 "nonce mismatch", login keeps waiting; then it times out.
    @Test fun `rejects a callback with a wrong nonce and keeps waiting, then times out`() {
        val captured = CompletableFuture<Captured>()
        val result = CompletableFuture.supplyAsync {
            Sso.runLoopbackLogin(
                buildUrl = { port, nonce -> captured.complete(Captured(port, nonce)); "http://x" },
                openUrl = { true },
                timeoutMs = 300,
            )
        }
        val c = captured.get(5, TimeUnit.SECONDS)
        val (status, body) = hitBody("http://127.0.0.1:${c.port}/callback?access_token=A&refresh_token=R&nonce=WRONG")

        assertEquals(400, status)
        assertTrue(body.contains("nonce mismatch"))

        val e = assertThrows(java.util.concurrent.ExecutionException::class.java) { result.get(5, TimeUnit.SECONDS) }
        assertTrue(e.cause?.message.orEmpty().contains("timed out"))
    }

    // (c) missing tokens -> 400 "missing tokens", distinct from the nonce-mismatch message.
    @Test fun `rejects a callback that is missing tokens with a distinct message`() {
        val captured = CompletableFuture<Captured>()
        val result = CompletableFuture.supplyAsync {
            Sso.runLoopbackLogin(
                buildUrl = { port, nonce -> captured.complete(Captured(port, nonce)); "http://x" },
                openUrl = { true },
                timeoutMs = 300,
            )
        }
        val c = captured.get(5, TimeUnit.SECONDS)
        val (status, body) = hitBody("http://127.0.0.1:${c.port}/callback?nonce=${c.nonce}")

        assertEquals(400, status)
        assertTrue(body.contains("missing tokens"))

        val e = assertThrows(java.util.concurrent.ExecutionException::class.java) { result.get(5, TimeUnit.SECONDS) }
        assertTrue(e.cause?.message.orEmpty().contains("timed out"))
    }

    // (d) timeoutMs = 200 with no callback ever arriving -> throws "timed out".
    @Test fun `times out waiting for the browser callback`() {
        val result = CompletableFuture.supplyAsync {
            Sso.runLoopbackLogin(
                buildUrl = { _, _ -> "http://x" },
                openUrl = { true },
                timeoutMs = 200,
            )
        }
        val e = assertThrows(java.util.concurrent.ExecutionException::class.java) { result.get(5, TimeUnit.SECONDS) }
        assertTrue(e.cause?.message.orEmpty().contains("timed out"))
    }

    // (e) onCancel invoked from another thread -> throws SsoCancelled and releases the port.
    @Test fun `cancelling rejects and releases the loopback port`() {
        val captured = CompletableFuture<Int>()
        val cancelFn = CompletableFuture<() -> Unit>()
        val result = CompletableFuture.supplyAsync {
            Sso.runLoopbackLogin(
                buildUrl = { port, _ -> captured.complete(port); "http://x" },
                openUrl = { true },
                timeoutMs = 60_000, // would hang the suite if cancel did not work
                onCancel = { cancel -> cancelFn.complete(cancel) },
            )
        }
        val port = captured.get(5, TimeUnit.SECONDS)
        assertTrue(port > 0)
        val cancel = cancelFn.get(5, TimeUnit.SECONDS)
        cancel()

        val e = assertThrows(java.util.concurrent.ExecutionException::class.java) { result.get(5, TimeUnit.SECONDS) }
        assertTrue(e.cause is SsoCancelled)
        assertTrue(portReleased(port))
    }

    // (f) openUrl returning false -> throws "Could not open the browser".
    @Test fun `openUrl returning false fails the sign-in`() {
        val result = CompletableFuture.supplyAsync {
            Sso.runLoopbackLogin(
                buildUrl = { _, _ -> "http://x" },
                openUrl = { false },
                timeoutMs = 5000,
            )
        }
        val e = assertThrows(java.util.concurrent.ExecutionException::class.java) { result.get(5, TimeUnit.SECONDS) }
        assertTrue(e.cause?.message.orEmpty().contains("Could not open the browser"))
    }

    // Nonce shape: 32 url-safe chars, and the bound port is passed to buildUrl.
    @Test fun `generates a 32+ char url-safe nonce and passes the bound port`() {
        val captured = CompletableFuture<Captured>()
        val result = CompletableFuture.supplyAsync {
            Sso.runLoopbackLogin(
                buildUrl = { port, nonce -> captured.complete(Captured(port, nonce)); "http://x" },
                openUrl = { true },
                timeoutMs = 200,
            )
        }
        val c = captured.get(5, TimeUnit.SECONDS)
        assertTrue(c.port > 0)
        assertTrue(c.nonce.matches(Regex("^[A-Za-z0-9_-]{32,}$")))

        try { result.get(5, TimeUnit.SECONDS) } catch (_: Exception) { /* expected timeout */ }
    }
}
