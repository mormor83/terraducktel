package com.terraducktel.jetbrains.api

import org.junit.Assert.*
import org.junit.Test

class ApiErrorTest {
    @Test fun `string detail becomes the message, status is kept`() {
        val e = ApiError.fromResponse(400, """{"detail":"nope"}""")
        assertEquals(400, e.status)
        assertEquals("nope", e.message)
    }

    @Test fun `FastAPI validation list becomes loc- msg joined`() {
        val e = ApiError.fromResponse(422, """{"detail":[{"loc":["body","command"],"msg":"field required"}]}""")
        assertEquals(422, e.status)
        assertEquals("command: field required", e.message)
    }

    @Test fun `other JSON falls back to HTTP status`() {
        val e = ApiError.fromResponse(500, """{"x":1}""")
        assertEquals(500, e.status)
        assertEquals("HTTP 500", e.message)
    }

    @Test fun `non-JSON body becomes the trimmed message`() {
        val e = ApiError.fromResponse(400, "oops")
        assertEquals(400, e.status)
        assertEquals("oops", e.message)
    }

    @Test fun `empty body falls back to HTTP status`() {
        val e = ApiError.fromResponse(502, "")
        assertEquals(502, e.status)
        assertEquals("HTTP 502", e.message)
    }

    @Test fun `a JSON array falls back to HTTP status`() {
        val e = ApiError.fromResponse(500, "[1,2,3]")
        assertEquals(500, e.status)
        assertEquals("HTTP 500", e.message)
    }

    @Test fun `a quoted JSON string falls back to HTTP status`() {
        val e = ApiError.fromResponse(500, "\"x\"")
        assertEquals(500, e.status)
        assertEquals("HTTP 500", e.message)
    }

    @Test fun `a bare JSON number falls back to HTTP status`() {
        val e = ApiError.fromResponse(500, "5")
        assertEquals(500, e.status)
        assertEquals("HTTP 500", e.message)
    }

    @Test fun `a bare JSON null falls back to HTTP status`() {
        val e = ApiError.fromResponse(500, "null")
        assertEquals(500, e.status)
        assertEquals("HTTP 500", e.message)
    }

    @Test fun `unparseable multi-word text becomes the trimmed message`() {
        val e = ApiError.fromResponse(400, "not json at all")
        assertEquals(400, e.status)
        assertEquals("not json at all", e.message)
    }
}
