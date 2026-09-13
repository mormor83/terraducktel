package com.terraducktel.jetbrains.api

import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import java.net.URLEncoder
import java.nio.charset.StandardCharsets.UTF_8
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.ExecutionException

/** Supplied by the token manager. The client never stores tokens itself. */
interface TokenProvider {
    /** May block (secret store load / lazy refresh). */
    fun getAccessToken(): String?
    /** Obtain a fresh access token (refresh flow, or re-read an API key). Returns null when the
     *  credential is definitively dead; throws on a transient failure (network, timeout, 5xx). */
    fun refreshAccessToken(): String?
    /** Whether a long-lived credential is stored at all — which is what separates "never signed
     *  in" from "signed in, but the stored refresh token is dead". Safe to read synchronously
     *  immediately after [getAccessToken], which has already done any secret-store load. */
    fun hasCredential(): Boolean
    fun signOut()
}

/** Sign-out (and refresh) coalescing state, shared by a client and every clone [TdtClient.withBu]
 *  makes of it — a single auth session (and its listeners) spans all of them, so a 401 seen
 *  through any clone signs the whole session out exactly once, and concurrent 401s across the
 *  parent and its clones share one in-flight refresh rather than each redeeming the refresh
 *  token. */
class AuthState {
    @Volatile var epoch: Int = 0
    var refreshing: CompletableFuture<String?>? = null
    var signingOut: CompletableFuture<Void>? = null
    val listeners = CopyOnWriteArrayList<() -> Unit>()
    val lock = Any()
}

/** Blocking, typed TDT API client — a port of `services/vscode/src/api/client.ts`. Every method
 *  throws [ApiError] for an HTTP response of 400+ or [java.io.IOException] for a transport
 *  failure. Call off the UI thread. */
class TdtClient(
    val baseUrl: String,
    val bu: String,
    private val tokens: TokenProvider,
    private val insecureTls: Boolean = false,
    private val trace: ((String) -> Unit)? = null,
    private val auth: AuthState = AuthState(),
) {
    fun withBu(bu: String) = TdtClient(baseUrl, bu, tokens, insecureTls, trace, auth)
    fun onSignedOut(l: () -> Unit): () -> Unit { auth.listeners += l; return { auth.listeners -= l } }

    // ─── core ────────────────────────────────────────────────────────────────
    private fun url(path: String, query: Map<String, Any?> = emptyMap()): String {
        val qs = query.entries.filter { it.value != null && it.value != "" }.joinToString("&") { (k, v) ->
            val s = if (v is List<*>) v.joinToString(",") else v.toString()
            "${URLEncoder.encode(k, UTF_8)}=${URLEncoder.encode(s, UTF_8)}"
        }
        return baseUrl.trimEnd('/') + "/api/v1" + path + (if (qs.isEmpty()) "" else "?$qs")
    }

    private inline fun <reified R> send(method: String, path: String, query: Map<String, Any?> = emptyMap(), body: String? = null, auth: Boolean = true): R {
        val text = sendRaw(method, path, query, body, auth)
        return if (text.isEmpty() || R::class == Unit::class) Unit as R else TdtJson.decodeFromString(text)
    }

    private fun sendRaw(method: String, path: String, query: Map<String, Any?>, body: String?, useAuth: Boolean): String {
        // Captured before any I/O: identifies which sign-out cycle this request belongs to,
        // regardless of how long it (or the refresh it may trigger) takes afterwards.
        val epoch = auth.epoch
        fun attempt(token: String?): HttpResponse {
            val h = HashMap<String, String>()
            if (useAuth) { token?.let { h["Authorization"] = "Bearer $it" }; if (bu.isNotEmpty()) h["X-Business-Unit"] = bu }
            val t0 = System.currentTimeMillis()
            val res = HttpTransport.request(method, url(path, query), h, body, insecureTls)
            trace?.invoke("$method $path → ${res.status} (${System.currentTimeMillis() - t0} ms)")
            return res
        }
        var token: String? = null
        if (useAuth) {
            token = tokens.getAccessToken()
            if (token == null) {
                // Two different states arrive here and they must not be conflated:
                //
                // A stored credential that can no longer mint an access token is a DEAD session —
                // e.g. after a restart there is no access token in memory, so the lazy refresh ran
                // and the server rejected the refresh token. Sign out for real; otherwise a caller
                // that only checks "is there a credential" would poll forever against a dead one.
                if (tokens.hasCredential()) { signOutOnce(epoch); throw ApiError(401, "Session expired — sign in again") }
                // No credential at all: "never signed in", not "token expired". Fail closed WITHOUT
                // touching the network, the refresh flow, or the sign-out listeners — otherwise a
                // poll firing while signed out would fire a "session expired" listener for a user
                // who never had a session.
                throw ApiError(401, "Not signed in")
            }
        }
        var res = attempt(token); var used = token
        if (useAuth && res.status == 401) {
            val current = tokens.getAccessToken()
            val fresh = if (current != null && current != used) current else refreshOnce()
            if (fresh != null) { res = attempt(fresh); used = fresh }
            if (res.status == 401) signOutOnce(epoch)
        }
        if (res.status >= 400) throw ApiError.fromResponse(res.status, res.text)
        return res.text
    }

    /** Coalesce parallel 401s into one refresh (one redemption of the rotating refresh token). The
     *  first caller under the lock creates the future and performs the refresh on its own thread;
     *  everyone else waits on that future. A rejection propagates unchanged (transient failure
     *  must fail the original request, never sign the user out): the token manager only resolves
     *  null for a definitive rejection of the refresh token, and throws for anything transient
     *  (network down, timeout, 5xx) — signing the user out because the API was briefly
     *  unreachable would delete a perfectly good credential. */
    private fun refreshOnce(): String? {
        var mine = false
        val f = synchronized(auth.lock) {
            auth.refreshing ?: CompletableFuture<String?>().also { auth.refreshing = it; mine = true }
        }
        if (mine) {
            try { f.complete(tokens.refreshAccessToken()) } catch (t: Throwable) { f.completeExceptionally(t) }
            finally { synchronized(auth.lock) { if (auth.refreshing === f) auth.refreshing = null } }
        }
        try { return f.get() } catch (e: ExecutionException) { throw e.cause ?: e }
    }

    /** Coalesce concurrent terminal-401s into one sign-out per auth epoch: a request that started
     *  before the current sign-out cycle began (its captured epoch matches the live one) triggers
     *  — and shares — that cycle; a request from a superseded epoch just joins whatever cycle is
     *  current. The epoch only advances on an actual sign-out, so an unrelated long-lived request
     *  (e.g. a poll) sitting in flight across a re-sign-in can never block or wedge the next
     *  sign-out — there's no counter to fail to drain. */
    private fun signOutOnce(epoch: Int) {
        var mine = false
        val f = synchronized(auth.lock) {
            if (epoch != auth.epoch) auth.signingOut
            else { auth.epoch++; CompletableFuture<Void>().also { auth.signingOut = it; mine = true } }
        } ?: return
        if (mine) {
            try { tokens.signOut(); auth.listeners.toList().forEach { it() }; f.complete(null) }
            catch (t: Throwable) { f.completeExceptionally(t) }
        }
        try { f.get() } catch (e: ExecutionException) { throw e.cause ?: e }
    }

    private fun enc(id: String): String = URLEncoder.encode(id, UTF_8)

    // ─── auth (public) ───────────────────────────────────────────────────────
    fun ssoLoginUrl(port: Int, nonce: String): String = url("/auth/oidc/login", mapOf("cli_port" to port, "cli_nonce" to nonce))
    fun authConfig(): AuthConfig = send("GET", "/auth/config", auth = false)
    fun login(email: String, password: String): TokenPair =
        send("POST", "/auth/token", body = TdtJson.encodeToString(LoginBody(email, password)), auth = false)
    fun refresh(refreshToken: String): TokenPair =
        send("POST", "/auth/refresh", body = TdtJson.encodeToString(RefreshBody(refreshToken)), auth = false)

    // ─── data ────────────────────────────────────────────────────────────────
    fun listBusinessUnits(): List<BusinessUnit> = send("GET", "/business-units")
    fun listWorkspaces(): List<Workspace> = send("GET", "/workspaces")
    fun updateWorkspace(id: String, repoRef: String): Workspace =
        send("PUT", "/workspaces/${enc(id)}", body = TdtJson.encodeToString(RepoRefPatch(repoRef)))
    fun listBranches(id: String): Branches = send("GET", "/workspaces/${enc(id)}/branches")
    fun syncWorkspace(id: String): Unit = send("POST", "/workspaces/${enc(id)}/sync")
    fun triggerRun(id: String, body: TriggerRunBody): Run =
        send("POST", "/workspaces/${enc(id)}/runs", body = TdtJson.encodeToString(body))
    fun listRuns(limit: Int? = null, status: List<String>? = null, workspaceId: String? = null): List<Run> =
        send("GET", "/runs", query = mapOf("limit" to limit, "status" to status, "workspace_id" to workspaceId))
    fun getRun(id: String): Run = send("GET", "/runs/${enc(id)}")
    fun getSteps(id: String, since: Int? = null, includeOutput: Boolean = true): List<RunStep> =
        send("GET", "/runs/${enc(id)}/steps", query = mapOf("since" to since, "include_output" to (if (includeOutput) null else "false")))
    fun getGraph(id: String): RunGraph = send("GET", "/runs/${enc(id)}/graph")
    fun getPlan(id: String): PlanOutput = send("GET", "/runs/${enc(id)}/plan")
    fun approve(id: String): Unit = send("POST", "/runs/${enc(id)}/approve")
    fun reject(id: String, reason: String? = null): Unit =
        send("POST", "/runs/${enc(id)}/reject", body = if (!reason.isNullOrBlank()) TdtJson.encodeToString(RejectBody(reason)) else null)
    fun cancel(id: String): Unit = send("POST", "/runs/${enc(id)}/cancel")
}
