package com.terraducktel.jetbrains.session

import com.intellij.openapi.application.ApplicationManager
import com.intellij.testFramework.PlatformTestUtil
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.terraducktel.jetbrains.auth.PasswordSafeSecretStore
import com.terraducktel.jetbrains.settings.Profile
import com.terraducktel.jetbrains.settings.TdtSettings
import com.terraducktel.jetbrains.testutil.InMemorySecretStore
import com.terraducktel.jetbrains.testutil.StubServer
import java.util.Base64
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/**
 * Platform tests for [TdtSession]: reload/profile wiring, API-key + password sign-in, BU
 * switching, sign-out and [TdtSessionListener] notifications. Port of the relevant `session.ts`
 * behaviours (see brief for Task 7).
 *
 * [TdtSession] is an app-level light service, so it (and [TdtSettings]) persist across tests
 * within one JVM — [setUp] injects an in-memory secret store via the `secretStoreFactory` test
 * seam and [tearDown] signs out, restores [TdtSettings], and puts the real PasswordSafe-backed
 * factory back so a later, unrelated test never sees this test's fake store.
 */
class TdtSessionTest : BasePlatformTestCase() {

    private val session get() = TdtSession.getInstance()
    private lateinit var secretStore: InMemorySecretStore

    override fun setUp() {
        super.setUp()
        secretStore = InMemorySecretStore()
        session.secretStoreFactory = { secretStore }
    }

    override fun tearDown() {
        try {
            offEdt { session.signOut() }
            TdtSettings.getInstance().loadState(TdtSettings.State())
            session.secretStoreFactory = { PasswordSafeSecretStore() }
            offEdt { session.reload() }
        } finally {
            super.tearDown()
        }
    }

    /** Runs a blocking [TdtSession] call on a pooled thread and waits for it — mirrors production
     *  usage, where actions call these off the EDT via `ActionUtil.runBackground`. */
    private fun <T> offEdt(block: () -> T): T =
        ApplicationManager.getApplication().executeOnPooledThread<T> { block() }.get(10, TimeUnit.SECONDS)

    private fun fakeJwt(vararg claims: Pair<String, String>): String {
        val json = claims.joinToString(",", prefix = "{", postfix = "}") { (k, v) -> "\"$k\":\"$v\"" }
        val payload = Base64.getUrlEncoder().withoutPadding().encodeToString(json.toByteArray(Charsets.UTF_8))
        return "h.$payload.s"
    }

    private fun setProfile(srv: StubServer, name: String = "p"): Profile {
        val profile = Profile(name = name, url = srv.url)
        TdtSettings.getInstance().state.profiles = mutableListOf(profile)
        TdtSettings.getInstance().state.activeProfile = name
        return profile
    }

    // (a) sign in with an API key and hit an authenticated endpoint.
    fun testSignInWithApiKeySignsInAndCarriesTheBearerToken() {
        StubServer().use { srv ->
            srv.json("GET", "/api/v1/auth/config", 200, """{"mode":"local"}""")
            srv.json("GET", "/api/v1/workspaces", 200, "[]")
            setProfile(srv)
            offEdt { session.reload() }

            offEdt { session.signInWithApiKey("tdt_x") }

            assertTrue(session.isSignedIn())
            val result = offEdt { session.client!!.listWorkspaces() }
            assertTrue(result.isEmpty())
            val call = srv.calls("GET", "/api/v1/workspaces").single()
            assertEquals("Bearer tdt_x", call.headers["authorization"])
        }
    }

    // (b) setBu persists to settings and is sent on subsequent requests.
    fun testSetBuPersistsAndIsSentOnRequests() {
        StubServer().use { srv ->
            srv.json("GET", "/api/v1/workspaces", 200, "[]")
            setProfile(srv)
            offEdt { session.reload() }
            offEdt { session.signInWithApiKey("tdt_x") }

            offEdt { session.setBu("ops") }

            assertEquals("ops", TdtSettings.getInstance().state.buByProfile["p"])
            offEdt { session.client!!.listWorkspaces() }
            val call = srv.calls("GET", "/api/v1/workspaces").last()
            assertEquals("ops", call.headers["x-business-unit"])
        }
    }

    // (c) signOut clears the client and deletes the PasswordSafe entry.
    fun testSignOutClearsClientAndSecret() {
        StubServer().use { srv ->
            setProfile(srv)
            offEdt { session.reload() }
            offEdt { session.signInWithApiKey("tdt_x") }
            assertNotNull(secretStore.get("terraducktel.cred.p"))

            offEdt { session.signOut() }

            assertNull(session.clientOrNull())
            assertNull(secretStore.get("terraducktel.cred.p"))
        }
    }

    // (d) canWrite(): API key -> true; JWT viewer -> false; JWT operator -> true.
    fun testCanWriteForApiKeyViewerAndOperator() {
        StubServer().use { srv ->
            setProfile(srv)
            offEdt { session.reload() }

            offEdt { session.signInWithApiKey("tdt_x") }
            assertTrue(session.canWrite())
            offEdt { session.signOut() }

            srv.json("POST", "/api/v1/auth/token", 200, """{"access_token":"${fakeJwt("role" to "viewer")}","refresh_token":"r1"}""")
            offEdt { session.signInWithPassword("a@b", "pw") }
            assertFalse(session.canWrite())
            offEdt { session.signOut() }

            srv.on("POST", "/api/v1/auth/token") { _, ex ->
                StubServer.respond(ex, 200, """{"access_token":"${fakeJwt("role" to "operator")}","refresh_token":"r2"}""")
            }
            offEdt { session.signInWithPassword("a@b", "pw") }
            assertTrue(session.canWrite())
        }
    }

    // (e) TdtSessionListener fires on sign-in and sign-out.
    fun testSessionListenerFiresOnSignInAndSignOut() {
        StubServer().use { srv ->
            setProfile(srv)
            offEdt { session.reload() }

            val count = AtomicInteger()
            ApplicationManager.getApplication().messageBus.connect(testRootDisposable).subscribe(
                TdtSessionListener.TOPIC,
                object : TdtSessionListener {
                    override fun sessionChanged() { count.incrementAndGet() }
                },
            )

            offEdt { session.signInWithApiKey("tdt_x") }
            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
            val afterSignIn = count.get()
            assertTrue("expected sessionChanged to fire on sign-in", afterSignIn > 0)

            offEdt { session.signOut() }
            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
            assertTrue("expected sessionChanged to fire again on sign-out", count.get() > afterSignIn)
        }
    }
}
