package com.terraducktel.jetbrains.auth

import com.terraducktel.jetbrains.api.ApiError
import com.terraducktel.jetbrains.api.TdtClient
import com.terraducktel.jetbrains.testutil.StubServer
import org.junit.Assert.*
import org.junit.Test
import java.util.Base64
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/** Port of `services/vscode/test/unit/tokenManager.test.ts`. */
class TokenManagerTest {

    /** In-memory [SecretStore] — the Kotlin equivalent of the TS suite's `MemorySecretStore`. */
    private class InMemorySecretStore : SecretStore {
        private val map = HashMap<String, String>()
        override fun get(key: String): String? = synchronized(map) { map[key] }
        override fun set(key: String, value: String) { synchronized(map) { map[key] = value } }
        override fun delete(key: String) { synchronized(map) { map.remove(key) } }
    }

    /** Wraps another store and counts + delays `get` calls, to prove concurrent first callers
     *  share a single secret-store read rather than each doing their own. */
    private class DelayedCountingStore(private val delegate: SecretStore, private val delayMs: Long = 20) : SecretStore {
        val getCalls = AtomicInteger()
        override fun get(key: String): String? {
            getCalls.incrementAndGet()
            Thread.sleep(delayMs)
            return delegate.get(key)
        }
        override fun set(key: String, value: String) = delegate.set(key, value)
        override fun delete(key: String) = delegate.delete(key)
    }

    private fun fakeJwt(vararg claims: Pair<String, String>): String {
        val json = claims.joinToString(",", prefix = "{", postfix = "}") { (k, v) -> "\"$k\":\"$v\"" }
        val payload = Base64.getUrlEncoder().withoutPadding().encodeToString(json.toByteArray(Charsets.UTF_8))
        return "h.$payload.s"
    }

    // 1. restore() with nothing stored -> signed out, no token, no credential.
    @Test fun `restore with nothing stored leaves the manager signed out`() {
        val store = InMemorySecretStore()
        val tm = TokenManager(store, "prod")

        tm.restore()

        assertFalse(tm.isSignedIn())
        assertNull(tm.getAccessToken())
        assertFalse(tm.hasCredential())
    }

    // 2. signInWithApiKey persists the key, returns it as the access token, no claims.
    @Test fun `signInWithApiKey persists the key and returns it as the access token`() {
        val store = InMemorySecretStore()
        val tm = TokenManager(store, "prod")

        tm.signInWithApiKey("tdt_abc")

        assertEquals("tdt_abc", tm.getAccessToken())
        assertNull(tm.claims())
        assertEquals("""{"kind":"api_key","api_key":"tdt_abc"}""", store.get("terraducktel.cred.prod"))
    }

    // 2b. a key without the tdt_ prefix is rejected and nothing is stored.
    @Test fun `signInWithApiKey rejects a key without the tdt_ prefix and stores nothing`() {
        val store = InMemorySecretStore()
        val tm = TokenManager(store, "prod")

        val e = assertThrows(IllegalArgumentException::class.java) { tm.signInWithApiKey("nope") }

        assertEquals("That doesn't look like a TDT API key (expected tdt_…)", e.message)
        assertNull(store.get("terraducktel.cred.prod"))
        assertFalse(tm.isSignedIn())
    }

    // 3. signInWithPassword logs in via the client, stores only the refresh token, and the access
    //    token's claims are decodable.
    @Test fun `signInWithPassword stores the refresh token and exposes decoded claims`() {
        StubServer().use { srv ->
            val access = fakeJwt("email" to "a@b", "role" to "operator")
            srv.json("POST", "/api/v1/auth/token", 200, """{"access_token":"$access","refresh_token":"r1"}""")
            val store = InMemorySecretStore()
            val tm = TokenManager(store, "prod")
            val client = TdtClient(srv.url, "default", tm)
            tm.attach(client)

            tm.signInWithPassword("a@b", "pw")

            assertEquals(access, tm.getAccessToken())
            assertEquals("""{"kind":"password","refresh_token":"r1"}""", store.get("terraducktel.cred.prod"))
            assertEquals("a@b", tm.claims()?.email)
            assertEquals("operator", tm.claims()?.role)
            assertTrue(tm.isSignedIn())
        }
    }

    // 4. lazy refresh: a fresh manager over a store holding a password credential (no access
    //    token in memory) refreshes once on the first getAccessToken() and persists the rotated
    //    refresh token.
    @Test fun `lazy refresh posts once and persists the rotated refresh token`() {
        StubServer().use { srv ->
            val store = InMemorySecretStore()
            store.set("terraducktel.cred.prod", """{"kind":"password","refresh_token":"r1"}""")
            val newAccess = fakeJwt("role" to "viewer")
            srv.json("POST", "/api/v1/auth/refresh", 200, """{"access_token":"$newAccess","refresh_token":"r2"}""")
            val tm = TokenManager(store, "prod")
            val client = TdtClient(srv.url, "default", tm)
            tm.attach(client)

            val token = tm.getAccessToken()

            assertEquals(newAccess, token)
            assertEquals(1, srv.calls("POST", "/api/v1/auth/refresh").size)
            assertEquals("""{"refresh_token":"r1"}""", srv.calls("POST", "/api/v1/auth/refresh").single().body)
            assertEquals("""{"kind":"password","refresh_token":"r2"}""", store.get("terraducktel.cred.prod"))
        }
    }

    // 5. Concurrent getAccessToken() from 4 threads on a cold manager coalesces into exactly one
    //    POST /auth/refresh (the refresh token rotates, so two redemptions would race).
    @Test fun `concurrent getAccessToken from 4 threads makes exactly one refresh request`() {
        StubServer().use { srv ->
            val store = InMemorySecretStore()
            store.set("terraducktel.cred.prod", """{"kind":"password","refresh_token":"r1"}""")
            srv.on("POST", "/api/v1/auth/refresh") { _, ex ->
                Thread.sleep(25)
                StubServer.respond(ex, 200, """{"access_token":"${fakeJwt("role" to "viewer")}","refresh_token":"r2"}""")
            }
            val tm = TokenManager(store, "prod")
            val client = TdtClient(srv.url, "default", tm)
            tm.attach(client)

            val pool = Executors.newFixedThreadPool(4)
            val results = try {
                val futures = (1..4).map { pool.submit<String?> { tm.getAccessToken() } }
                futures.map { it.get(10, TimeUnit.SECONDS) }
            } finally { pool.shutdown() }

            results.forEach { assertNotNull(it) }
            assertEquals(1, results.distinct().size)
            assertEquals(1, srv.calls("POST", "/api/v1/auth/refresh").size)
        }
    }

    // 6a. A 401 refresh is a definitive rejection: refreshAccessToken() returns null but the
    //     credential stays stored (the TdtClient is the one that signs out).
    @Test fun `a 401 refresh returns null but keeps the credential stored`() {
        StubServer().use { srv ->
            val store = InMemorySecretStore()
            store.set("terraducktel.cred.prod", """{"kind":"password","refresh_token":"dead"}""")
            srv.json("POST", "/api/v1/auth/refresh", 401, """{"detail":"invalid"}""")
            val tm = TokenManager(store, "prod")
            val client = TdtClient(srv.url, "default", tm)
            tm.attach(client)

            assertNull(tm.refreshAccessToken())

            assertEquals("""{"kind":"password","refresh_token":"dead"}""", store.get("terraducktel.cred.prod"))
            assertTrue(tm.isSignedIn())
        }
    }

    // 6b. A transient (5xx) refresh failure is rethrown, not swallowed, and the credential is
    //     left untouched.
    @Test fun `a transient 5xx refresh failure is rethrown and keeps the credential`() {
        StubServer().use { srv ->
            val store = InMemorySecretStore()
            store.set("terraducktel.cred.prod", """{"kind":"password","refresh_token":"r1"}""")
            srv.json("POST", "/api/v1/auth/refresh", 502, """{"detail":"bad gateway"}""")
            val tm = TokenManager(store, "prod")
            val client = TdtClient(srv.url, "default", tm)
            tm.attach(client)

            val e = assertThrows(ApiError::class.java) { tm.refreshAccessToken() }

            assertEquals(502, e.status)
            assertEquals("""{"kind":"password","refresh_token":"r1"}""", store.get("terraducktel.cred.prod"))
            assertTrue(tm.isSignedIn())
        }
    }

    // 7. signOut() deletes the stored secret and fires listeners.
    @Test fun `signOut deletes the secret and fires listeners`() {
        val store = InMemorySecretStore()
        val tm = TokenManager(store, "prod")
        tm.signInWithApiKey("tdt_abc")
        var fired = 0
        tm.onDidChange { fired++ }

        tm.signOut()

        assertFalse(tm.isSignedIn())
        assertNull(store.get("terraducktel.cred.prod"))
        assertEquals(1, fired)
    }

    // 8. A refresh that completes AFTER signOut() ran mid-flight must not resurrect the
    //    credential: the rotated token belongs to a session that no longer exists.
    @Test fun `a refresh landing after signOut does not resurrect the credential`() {
        StubServer().use { srv ->
            val store = InMemorySecretStore()
            store.set("terraducktel.cred.prod", """{"kind":"password","refresh_token":"r1"}""")
            srv.on("POST", "/api/v1/auth/refresh") { _, ex ->
                Thread.sleep(40)
                StubServer.respond(ex, 200, """{"access_token":"${fakeJwt("role" to "viewer")}","refresh_token":"r2"}""")
            }
            val tm = TokenManager(store, "prod")
            val client = TdtClient(srv.url, "default", tm)
            tm.attach(client)

            val pool = Executors.newSingleThreadExecutor()
            try {
                val inFlight = pool.submit<String?> { tm.refreshAccessToken() }
                Thread.sleep(10)
                tm.signOut()                                   // user signs out mid-redemption
                assertNull(inFlight.get(10, TimeUnit.SECONDS))
            } finally { pool.shutdown() }

            assertFalse(tm.isSignedIn())
            assertNull(store.get("terraducktel.cred.prod"))    // "r2" was never written back
        }
    }

    // Extra: restores a stored credential on construction.
    @Test fun `restores a stored credential on construction`() {
        val store = InMemorySecretStore()
        store.set("terraducktel.cred.prod", """{"kind":"api_key","api_key":"tdt_k"}""")
        val tm = TokenManager(store, "prod")

        tm.restore()

        assertTrue(tm.isSignedIn())
        assertEquals("tdt_k", tm.getAccessToken())
    }

    // Extra: concurrent first getAccessToken() calls (no explicit restore()) share one secret
    // store read rather than each doing their own.
    @Test fun `concurrent first getAccessToken calls share one secret-store read`() {
        val base = InMemorySecretStore()
        base.set("terraducktel.cred.prod", """{"kind":"api_key","api_key":"tdt_k"}""")
        val delayed = DelayedCountingStore(base)
        val tm = TokenManager(delayed, "prod")

        val pool = Executors.newFixedThreadPool(2)
        val results = try {
            val futures = (1..2).map { pool.submit<String?> { tm.getAccessToken() } }
            futures.map { it.get(10, TimeUnit.SECONDS) }
        } finally { pool.shutdown() }

        assertEquals(listOf("tdt_k", "tdt_k"), results)
        assertEquals(1, delayed.getCalls.get())
    }
}
