package com.terraducktel.jetbrains.api

import com.terraducktel.jetbrains.testutil.StubServer
import org.junit.Assert.*
import org.junit.Test

class HttpTransportTest {
    @Test fun `sends json body and headers, returns status and text`() = StubServer().use { srv ->
        srv.on("POST", "/echo") { c, ex -> StubServer.respond(ex, 201, """{"got":${c.body},"auth":"${c.headers["authorization"]}"}""") }
        val r = HttpTransport.request("POST", "${srv.url}/echo", mapOf("Authorization" to "Bearer t"), """{"a":1}""", insecureTls = false)
        assertEquals(201, r.status)
        assertTrue(r.text, r.text.contains("\"got\":{\"a\":1}") && r.text.contains("Bearer t"))
        assertEquals("application/json", srv.calls.single().headers["content-type"])
        assertEquals("application/json", srv.calls.single().headers["accept"])
    }
    @Test fun `error bodies are returned, not thrown`() = StubServer().use { srv ->
        srv.json("GET", "/nope", 422, """{"detail":"bad"}""")
        val r = HttpTransport.request("GET", "${srv.url}/nope", emptyMap(), null, insecureTls = false)
        assertEquals(422, r.status); assertEquals("""{"detail":"bad"}""", r.text)
    }
    @Test fun `204 yields empty text`() = StubServer().use { srv ->
        srv.on("POST", "/x") { _, ex -> ex.sendResponseHeaders(204, -1); ex.close() }
        assertEquals("", HttpTransport.request("POST", "${srv.url}/x", emptyMap(), null, insecureTls = false).text)
    }
    @Test(expected = java.io.IOException::class) fun `connection refused throws IOException`() {
        HttpTransport.request("GET", "http://127.0.0.1:9/x", emptyMap(), null, insecureTls = false, connectTimeoutMs = 500)
    }
    @Test(expected = java.io.IOException::class) fun `connection refused with a body throws IOException`() {
        HttpTransport.request("POST", "http://127.0.0.1:9/x", emptyMap(), """{"a":1}""", insecureTls = false, connectTimeoutMs = 500)
    }
}
