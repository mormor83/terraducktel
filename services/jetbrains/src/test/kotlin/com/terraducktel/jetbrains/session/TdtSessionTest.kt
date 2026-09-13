package com.terraducktel.jetbrains.session

import com.intellij.openapi.application.ApplicationManager
import com.intellij.testFramework.PlatformTestUtil
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.terraducktel.jetbrains.api.ApiError
import com.terraducktel.jetbrains.auth.PasswordSafeSecretStore
import com.terraducktel.jetbrains.auth.SecretStore
import com.terraducktel.jetbrains.settings.Profile
import com.terraducktel.jetbrains.settings.TdtSettings
import com.terraducktel.jetbrains.state.Store
import com.terraducktel.jetbrains.testutil.InMemorySecretStore
import com.terraducktel.jetbrains.testutil.StubServer
import java.util.Base64
import java.util.concurrent.CountDownLatch
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
        // TdtSession.reload()/setBu() now kick off an async Store.refresh() against whatever
        // client is current at the moment that coroutine actually runs — which races arbitrarily
        // against this test's own synchronous steps (e.g. a sign-in landing between reload()'s
        // launch and the refresh coroutine's read of the client). Neutering the client provider
        // makes every such background refresh a pure no-op clear(), so it can never sneak an
        // extra request into a test's own call-count assertions; Store's real fetch behaviour is
        // covered independently by the plain-JUnit StoreTest.
        Store.getInstance().clientProvider = { null }
    }

    override fun tearDown() {
        try {
            offEdt { session.signOut() }
            TdtSettings.getInstance().loadState(TdtSettings.State())
            session.secretStoreFactory = { PasswordSafeSecretStore() }
            Store.getInstance().clientProvider = { TdtSession.getInstance().clientOrNull() }
            offEdt { session.reload() }
            // reload()/signOut() publish sessionChanged via invokeLater — drain it now so it isn't
            // left sitting on the EDT queue where a LATER test's message-bus subscription (checked
            // at dispatch time, not at publish() time) would pick it up and retroactively count it.
            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
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

    // (f) reload()/setActiveProfile() are @Synchronized: a reload() cycle that is slow to read its
    // secret store must not clobber a later profile switch that runs (necessarily queued behind
    // it — the lock spans the whole cycle, see TdtSession's class doc) once it unblocks. This
    // proves the mutex + generation-guard combination leaves no window for a stale cycle to win.
    //
    // Note on counting: sessionChanged carries no payload and is dispatched via invokeLater, and
    // message-bus delivery is resolved against the CURRENT subscriber list at DISPATCH time, not
    // at publish() time — so a listener can only be attributed to "which cycle" by draining the
    // EDT (and subscribing) before triggering the next one, never by re-reading session.profile
    // from inside the callback (by the time two publishes are both dispatched, both would already
    // observe the final state). Hence: count TOTAL notifications (deterministically 2 — one per
    // completed cycle, since full serialisation means neither cycle is ever silently dropped) and
    // assert the FINAL committed state separately.
    fun testSlowFirstReloadDoesNotClobberALaterProfileSwitch() {
        StubServer().use { srv ->
            val p1 = Profile(name = "p1", url = srv.url)
            val p2 = Profile(name = "p2", url = srv.url)
            TdtSettings.getInstance().state.profiles = mutableListOf(p1, p2)
            TdtSettings.getInstance().state.activeProfile = "p1"

            val gate = CountDownLatch(1)
            val started = CountDownLatch(1)
            val blockingStore = object : SecretStore {
                override fun get(key: String): String? {
                    started.countDown()
                    assertTrue("gate was never released", gate.await(10, TimeUnit.SECONDS))
                    return secretStore.get(key)
                }
                override fun set(key: String, value: String) = secretStore.set(key, value)
                override fun delete(key: String) = secretStore.delete(key)
            }
            session.secretStoreFactory = { blockingStore }

            // Drain anything still queued (e.g. a preceding test's tearDown() reload()) before
            // installing our own listener — see the note above on dispatch-time subscription.
            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
            val totalPublishes = AtomicInteger()
            ApplicationManager.getApplication().messageBus.connect(testRootDisposable).subscribe(
                TdtSessionListener.TOPIC,
                object : TdtSessionListener {
                    override fun sessionChanged() { totalPublishes.incrementAndGet() }
                },
            )

            // Cycle 1 (profile p1): acquires the session lock and blocks inside tm.restore().
            val cycle1 = ApplicationManager.getApplication().executeOnPooledThread { session.reload() }
            assertTrue("cycle 1 never reached the blocking secret-store read", started.await(5, TimeUnit.SECONDS))

            // Cycle 2 (profile p2): necessarily queues behind cycle 1's monitor — it cannot even
            // read settings until cycle 1 releases the lock.
            session.secretStoreFactory = { secretStore }
            val cycle2 = ApplicationManager.getApplication().executeOnPooledThread { session.setActiveProfile("p2") }

            gate.countDown() // let cycle 1's restore() return so it can finish and release the lock
            cycle1.get(10, TimeUnit.SECONDS)
            cycle2.get(10, TimeUnit.SECONDS)
            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()

            assertEquals("p2", session.profile?.name)
            assertEquals(srv.url, session.client?.baseUrl)
            assertEquals(2, totalPublishes.get()) // cycle 1's own completion, then cycle 2's — neither lost, neither duplicated
        }
    }

    // (g) The observable half of "a superseded cycle's session-expired listener is ignored": once
    // a second reload() has replaced tokens/client, the OLD client can no longer produce a
    // session-expired balloon/publish, even if something still holds a reference to it and drives
    // it into a 401. In this implementation that's because reload() proactively removes the
    // previous cycle's listener up front (TdtSession.cycleRemovers) — the in-body
    // `if (tokens !== cycleTokens)` identity guard exists for defence in depth, but exercising
    // THAT branch specifically (as opposed to the removal) would need the old client to be mid
    // callback — already past the identity check — at the exact instant a concurrent reload()
    // swaps `tokens`, which isn't reachable through the public API without adding test-only hooks
    // into TdtClient/AuthState. That narrower race is not covered here.
    fun testSessionExpiredListenerFromASupersededCycleDoesNotPublish() {
        StubServer().use { srv ->
            setProfile(srv)
            offEdt { session.reload() }
            offEdt { session.signInWithApiKey("tdt_x") }
            val oldClient = session.client!!

            // A second reload() rebuilds a fresh TokenManager/TdtClient and, at its top, removes
            // the previous cycle's onSignedOut/onDidChange registrations.
            offEdt { session.reload() }

            // Drain everything scheduled so far (this test's own setup publishes, and anything
            // left over from a preceding test) before installing our own counting listener —
            // message-bus delivery is resolved at dispatch time, not at publish() time, so
            // anything still queued would otherwise be retroactively counted below.
            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
            val count = AtomicInteger()
            ApplicationManager.getApplication().messageBus.connect(testRootDisposable).subscribe(
                TdtSessionListener.TOPIC,
                object : TdtSessionListener { override fun sessionChanged() { count.incrementAndGet() } },
            )

            srv.json("GET", "/api/v1/workspaces", 401, """{"detail":"expired"}""")
            val err = try { oldClient.listWorkspaces(); null } catch (e: ApiError) { e }
            assertNotNull(err)
            assertEquals(401, err!!.status)

            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
            assertEquals(0, count.get())
        }
    }
}
