package com.terraducktel.jetbrains.auth

import org.junit.Assert.*
import org.junit.Test
import java.util.Base64

/** Port of `services/vscode/test/unit/jwt.test.ts`. */
class JwtTest {
    private fun b64(json: String): String = Base64.getUrlEncoder().withoutPadding().encodeToString(json.toByteArray(Charsets.UTF_8))

    @Test fun `decodes the payload segment`() {
        val header = b64("""{"alg":"HS256"}""")
        val payload = b64("""{"sub":"u1","email":"a@b","role":"operator","is_superadmin":false,"type":"access","exp":1}""")
        val token = "$header.$payload.sig"

        val claims = Jwt.decodePayload(token)

        assertNotNull(claims)
        assertEquals("a@b", claims!!.email)
        assertEquals("operator", claims.role)
        assertEquals(false, claims.is_superadmin)
    }

    @Test fun `returns null for garbage and API keys`() {
        assertNull(Jwt.decodePayload("tdt_abc"))
        assertNull(Jwt.decodePayload("a.b"))
    }
}
