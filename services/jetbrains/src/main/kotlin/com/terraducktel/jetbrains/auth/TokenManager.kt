package com.terraducktel.jetbrains.auth

import com.terraducktel.jetbrains.api.ApiError
import com.terraducktel.jetbrains.api.TdtClient
import com.terraducktel.jetbrains.api.TdtJson
import com.terraducktel.jetbrains.api.TokenPair
import com.terraducktel.jetbrains.api.TokenProvider
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import java.io.InterruptedIOException
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.ExecutionException

/**
 * One per profile. Persists ONLY the long-lived secret (refresh token or API key); the access
 * token lives in memory and is re-minted on demand. Blocking port of
 * `services/vscode/src/auth/tokenManager.ts`.
 *
 * Concurrency notes (see `tokenManager.ts` for the async original):
 *  - `loadPromise` (a single shared in-flight/completed load) becomes a `loaded` flag guarded by
 *    `synchronized(this)`; `persist()` also marks it loaded, so a credential just written is
 *    never clobbered by a late `restore()`/lazy load re-reading the store.
 *  - `refreshing` becomes a `CompletableFuture<String?>?` created under the same lock by the
 *    first caller, which then performs the redemption on its own (calling) thread and completes
 *    the future; everyone else just `.get()`s it. The `finally` clause only clears the slot if it
 *    is still the one this call created (identity-guarded), so it can never cancel a newer
 *    flight's coalescing behind its back (e.g. a sign-out + re-sign-in mid-flight).
 *  - The lock is never held across network I/O or while invoking change listeners.
 */
class TokenManager(
    private val secrets: SecretStore,
    private val profileName: String,
) : TokenProvider {
    private var client: TdtClient? = null
    @Volatile private var access: String? = null
    @Volatile private var cred: StoredCredential? = null
    private val changeListeners = CopyOnWriteArrayList<() -> Unit>()

    private var loaded = false
    private var refreshing: CompletableFuture<String?>? = null

    private val key get() = "terraducktel.cred.$profileName"

    fun attach(client: TdtClient) { this.client = client }

    fun onDidChange(l: () -> Unit): () -> Unit {
        changeListeners += l
        return { changeListeners -= l }
    }
    private fun fire() { changeListeners.toList().forEach { it() } }

    /** Idempotent: only the first caller reads the secret store; concurrent callers — an explicit
     *  `restore()` and a lazy load from `getAccessToken()` alike — observe the same result. */
    fun restore() {
        synchronized(this) {
            if (loaded) return
            val raw = secrets.get(key)
            cred = raw?.let { TdtJson.decodeFromString<StoredCredential>(it) }
            access = if (cred?.kind == "api_key") cred?.api_key else null
            loaded = true
        }
    }
    private fun ensureLoaded() = restore()

    private fun persist(c: StoredCredential?) {
        synchronized(this) { loaded = true; cred = c }
        if (c != null) secrets.set(key, TdtJson.encodeToString(c)) else secrets.delete(key)
        fire()
    }

    fun isSignedIn(): Boolean = cred != null
    /** Synchronous by design — see [TokenProvider.hasCredential]. Callers that could run before
     *  the secret store has been read must call [restore] (or [getAccessToken]) first. */
    override fun hasCredential(): Boolean = cred != null
    fun kind(): String? = cred?.kind
    /** Claims from the current access token (null for API keys or when signed out). */
    fun claims(): AccessClaims? {
        val a = access ?: return null
        return if (cred?.kind != "api_key") Jwt.decodePayload(a) else null
    }

    fun signInWithPassword(email: String, password: String) {
        val c = client ?: throw IllegalStateException("TokenManager not attached to a client")
        signInWithTokenPair(c.login(email, password), "password")
    }
    fun signInWithTokenPair(pair: TokenPair, kind: String = "sso") {
        access = pair.access_token
        persist(StoredCredential(kind = kind, refresh_token = pair.refresh_token))
    }
    fun signInWithApiKey(key: String) {
        val k = key.trim()
        require(k.startsWith("tdt_")) { "That doesn't look like a TDT API key (expected tdt_…)" }
        access = k
        persist(StoredCredential(kind = "api_key", api_key = k))
    }
    override fun signOut() {
        access = null
        synchronized(this) { refreshing = null }
        persist(null)
    }

    // ─── TokenProvider ───────────────────────────────────────────────────────
    override fun getAccessToken(): String? {
        ensureLoaded()
        val c = cred ?: return null
        if (c.kind == "api_key") return c.api_key
        return access ?: refreshAccessToken()
    }

    /** Resolves a fresh access token, or null when the credential is definitively dead (the
     *  caller then signs out). Anything transient — the API unreachable, a timeout, a 5xx — is
     *  RETHROWN so the original request fails without destroying a credential that is very
     *  probably still valid. */
    override fun refreshAccessToken(): String? {
        ensureLoaded()
        val c = cred ?: return null
        if (c.kind == "api_key") return c.api_key
        val cl = client
        val refreshToken = c.refresh_token
        if (cl == null || refreshToken == null) return null

        var mine = false
        val f = synchronized(this) {
            refreshing ?: CompletableFuture<String?>().also { refreshing = it; mine = true }
        }
        if (mine) {
            try { f.complete(redeem(cl, c)) }
            catch (t: Throwable) { f.completeExceptionally(t) }
            finally { synchronized(this) { if (refreshing === f) refreshing = null } }
        }
        return try { f.get() }
        catch (e: ExecutionException) { throw e.cause ?: e }
        catch (e: InterruptedException) {
            Thread.currentThread().interrupt()
            throw InterruptedIOException("interrupted while waiting for token refresh")
        }
    }

    private fun redeem(client: TdtClient, cred: StoredCredential): String? = try {
        val pair = client.refresh(cred.refresh_token!!)
        if (this.cred !== cred) {
            // Signed out (or signed back in) while this redemption was in flight. The rotated
            // token belongs to a session that no longer exists: dropping it is right, writing it
            // over a cleared or brand-new credential would not be. Answer with whatever is
            // current.
            if (this.cred != null) access else null
        } else {
            access = pair.access_token
            persist(cred.copy(refresh_token = pair.refresh_token))
            access
        }
    } catch (e: ApiError) {
        // 4xx = the server rejected this refresh token (expired / revoked / wrong): the
        // credential is dead, so resolve null and let the caller sign out. Everything else is
        // transient.
        if (e.status in 400..499) null else throw e
    }
}
