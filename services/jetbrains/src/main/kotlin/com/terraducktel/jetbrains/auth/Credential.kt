package com.terraducktel.jetbrains.auth

import kotlinx.serialization.Serializable

/** Persisted secret for one profile — ONLY the long-lived credential (refresh token or API key);
 *  the access token lives in memory and is re-minted on demand. Port of `tokenManager.ts`'s
 *  `StoredCredential`. */
@Serializable
data class StoredCredential(
    val kind: String, // "password" | "api_key" | "sso"
    val refresh_token: String? = null,
    val api_key: String? = null,
)
