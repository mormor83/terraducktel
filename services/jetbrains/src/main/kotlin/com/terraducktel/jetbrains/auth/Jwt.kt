package com.terraducktel.jetbrains.auth

import com.terraducktel.jetbrains.api.TdtJson
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import java.util.Base64

@Serializable
data class AccessClaims(
    val sub: String? = null,
    val email: String? = null,
    val role: String? = null,
    val is_superadmin: Boolean? = null,
    val type: String? = null,
    val exp: Long? = null,
)

/** Decode (not verify) a JWT payload. Port of `jwt.ts`'s `decodeJwtPayload`. */
object Jwt {
    /** Returns null for anything that isn't a 3-part token, or whose payload segment fails to
     *  base64url-decode or parse as [AccessClaims]. */
    fun decodePayload(token: String): AccessClaims? {
        val parts = token.split(".")
        if (parts.size != 3) return null
        return runCatching {
            val segment = parts[1]
            val padded = segment + "=".repeat((4 - segment.length % 4) % 4)
            val json = String(Base64.getUrlDecoder().decode(padded), Charsets.UTF_8)
            TdtJson.decodeFromString<AccessClaims>(json)
        }.getOrNull()
    }
}
