package com.terraducktel.jetbrains.auth

import com.sun.net.httpserver.HttpServer
import com.terraducktel.jetbrains.api.TokenPair
import java.net.InetSocketAddress
import java.net.URLDecoder
import java.nio.charset.StandardCharsets.UTF_8
import java.security.SecureRandom
import java.util.Base64
import java.util.concurrent.CompletableFuture
import java.util.concurrent.ExecutionException
import java.util.concurrent.TimeUnit.MILLISECONDS
import java.util.concurrent.TimeoutException

/** Thrown by [Sso.runLoopbackLogin] when its `onCancel` handle is invoked. */
class SsoCancelled : RuntimeException("SSO sign-in cancelled")

private val DONE_HTML =
    "<!doctype html><meta charset=\"utf-8\"><title>Terraducktel</title>" +
        "<body style=\"font:14px system-ui;padding:3rem;text-align:center\">" +
        "<p>Signed in to Terraducktel for JetBrains IDEs.</p>" +
        "<p style=\"color:#666\">You can close this tab.</p></body>"

/**
 * Blocking port of `services/vscode/src/auth/sso.ts`: mirrors the tdt CLI's browser sign-in by
 * listening on `127.0.0.1:<random>`, sending the browser to the API's `/auth/oidc/login` with
 * `cli_port`+`cli_nonce` (via [buildUrl] / [openUrl]), and waiting for the server's page to
 * redirect back to `/callback?access_token&refresh_token&nonce`. The nonce must match.
 *
 * Callers run this on a background thread — it blocks until the browser calls back, the
 * timeout elapses, or [onCancel]'s handle is invoked.
 */
object Sso {
    fun runLoopbackLogin(
        buildUrl: (port: Int, nonce: String) -> String,
        openUrl: (String) -> Boolean,
        timeoutMs: Long = 5 * 60_000,
        onCancel: ((cancel: () -> Unit) -> Unit)? = null,
    ): TokenPair {
        val nonce = ByteArray(24).also { SecureRandom().nextBytes(it) }
            .let { Base64.getUrlEncoder().withoutPadding().encodeToString(it) } // 32 url-safe chars

        val future = CompletableFuture<TokenPair>()
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/") { exchange ->
            try {
                val uri = exchange.requestURI
                if (uri.path != "/callback") {
                    exchange.sendResponseHeaders(404, -1)
                    return@createContext
                }
                val params = parseQuery(uri.rawQuery)
                val got = params["nonce"]
                val access = params["access_token"]
                val refresh = params["refresh_token"]
                // Two very different failures, and telling them apart is the whole diagnosis: a
                // nonce mismatch means someone else's callback reached this listener, missing
                // tokens mean the server's redirect itself was malformed.
                if (got != nonce) {
                    val body = "Bad sign-in callback (nonce mismatch). Try signing in again.".toByteArray(UTF_8)
                    exchange.responseHeaders.add("Content-Type", "text/plain")
                    exchange.sendResponseHeaders(400, body.size.toLong())
                    exchange.responseBody.use { it.write(body) }
                    return@createContext
                }
                if (access == null || refresh == null) {
                    val body = "Bad sign-in callback (missing tokens). Try signing in again.".toByteArray(UTF_8)
                    exchange.responseHeaders.add("Content-Type", "text/plain")
                    exchange.sendResponseHeaders(400, body.size.toLong())
                    exchange.responseBody.use { it.write(body) }
                    return@createContext
                }
                val body = DONE_HTML.toByteArray(UTF_8)
                exchange.responseHeaders.add("Content-Type", "text/html; charset=utf-8")
                exchange.responseHeaders.add("Cache-Control", "no-store")
                exchange.sendResponseHeaders(200, body.size.toLong())
                exchange.responseBody.use { it.write(body) }
                future.complete(TokenPair(access_token = access, refresh_token = refresh))
            } finally {
                exchange.close()
            }
        }
        server.start()
        try {
            // Synchronous, before openUrl: a cancel arriving during the browser launch is
            // honoured rather than raced against it.
            onCancel?.invoke { future.completeExceptionally(SsoCancelled()) }

            val port = server.address.port
            val opened = try {
                openUrl(buildUrl(port, nonce))
            } catch (e: Exception) {
                future.completeExceptionally(e)
                false
            }
            if (!opened && !future.isDone) {
                future.completeExceptionally(RuntimeException("Could not open the browser for SSO sign-in"))
            }

            return try {
                future.get(timeoutMs, MILLISECONDS)
            } catch (e: TimeoutException) {
                throw RuntimeException("SSO sign-in timed out waiting for the browser callback")
            } catch (e: ExecutionException) {
                throw e.cause ?: e
            } catch (e: InterruptedException) {
                Thread.currentThread().interrupt()
                throw e
            }
        } finally {
            server.stop(0)
        }
    }

    /** Parses a raw query string (`k=v&k=v`) with URL-decoded keys/values; tokens are url-safe
     *  already but decoding is harmless and keeps this robust. */
    private fun parseQuery(rawQuery: String?): Map<String, String> {
        if (rawQuery.isNullOrEmpty()) return emptyMap()
        return rawQuery.split("&").mapNotNull { pair ->
            if (pair.isEmpty()) return@mapNotNull null
            val idx = pair.indexOf('=')
            val key = if (idx >= 0) pair.substring(0, idx) else pair
            val value = if (idx >= 0) pair.substring(idx + 1) else ""
            URLDecoder.decode(key, UTF_8) to URLDecoder.decode(value, UTF_8)
        }.toMap()
    }
}
