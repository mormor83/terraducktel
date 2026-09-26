package com.terraducktel.jetbrains.notifications

import com.intellij.notification.Notification
import com.intellij.notification.NotificationsManager
import com.intellij.openapi.application.ApplicationManager
import com.intellij.testFramework.PlatformTestUtil
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.api.TdtJson
import com.terraducktel.jetbrains.auth.PasswordSafeSecretStore
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.settings.Profile
import com.terraducktel.jetbrains.settings.TdtSettings
import com.terraducktel.jetbrains.testutil.InMemorySecretStore
import com.terraducktel.jetbrains.testutil.StubServer
import kotlinx.serialization.encodeToString
import java.util.concurrent.TimeUnit

/**
 * Platform coverage of [ApprovalService]'s wiring: it owns one [ApprovalWatcher] built around
 * [TdtSession] (client + who-is-signed-in) and [TdtSettings] (the poll interval + the persisted
 * dedupe set), [ApprovalService.rearm]s it on sign-in/sign-out, and turns a fresh awaiting-approval
 * run into a real IDE notification with the three actions [ApprovalNotifier] wires.
 *
 * Notifications are asserted via [NotificationsManager.getNotificationsOfType] — the platform's own
 * test-facing surface for "what did this project actually get shown" — rather than subscribing to
 * [com.intellij.notification.Notifications.TOPIC]: `Notification.notify()` routes differently under
 * `ApplicationManager.isUnitTestMode` (confirmed empirically — a `Notifications.TOPIC` subscription
 * observed nothing here even though the balloon really was posted, matching the brief's fallback:
 * "test ApprovalService's wiring through the watcher's notify callback instead"), and
 * [NotificationsManager] is the documented way to observe it regardless.
 *
 * [ApprovalService] is an app-level light service — like [TdtSession], it persists across tests
 * within one JVM — so [setUp]/[tearDown] follow [com.terraducktel.jetbrains.session.
 * TdtSessionTest]'s discipline: inject an in-memory secret store, drive everything through
 * `offEdt`, and restore every seam afterward. The light-fixture [project] is ALSO shared across
 * this class's own test methods (each `BasePlatformTestCase` method gets a fresh light project, but
 * [NotificationsManager] state isn't automatically scoped per-method the way `project` fields are),
 * so [setUp]/[tearDown] additionally [clearApprovalNotifications] — without it, a balloon posted by
 * one method would still be sitting in the manager when a LATER method's own "no balloon" assertion
 * runs, and whether that later method actually runs later depends on JUnit3 reflection order (which
 * varies across JDK builds) rather than declaration order.
 */
class ApprovalServiceTest : BasePlatformTestCase() {

    private val service get() = ApprovalService.getInstance()
    private val session get() = TdtSession.getInstance()
    private lateinit var secretStore: InMemorySecretStore

    override fun setUp() {
        super.setUp()
        secretStore = InMemorySecretStore()
        session.secretStoreFactory = { secretStore }
        // The real 15s floor (ApprovalService.intervalMs) is far too slow to drive from a test —
        // this seam lets a small `approvalsPollSeconds` translate into an actually-fast background
        // poll instead of being floored back up to 15000ms.
        service.minIntervalMs = 50L
        // A PRECEDING test method run in this same JVM (any test class, not just this one — the
        // notification manager isn't scoped per test class) may have left a balloon sitting in this
        // group; start every method from a clean slate rather than depending on execution order.
        clearApprovalNotifications()
    }

    override fun tearDown() {
        try {
            offEdt { session.signOut() }
            TdtSettings.getInstance().loadState(TdtSettings.State())
            session.secretStoreFactory = { PasswordSafeSecretStore() }
            service.minIntervalMs = 15_000L
            offEdt { session.reload() }
            offEdt { service.rearm() } // signed out now: stops the loop and forgets the primed key
            // reload()/signOut() publish sessionChanged via invokeLater — drain it now so it isn't
            // left sitting on the EDT queue where a LATER test's assertions would race it.
            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
            // Don't leave this method's own balloon(s) behind for whichever test happens to run
            // next — see the class doc.
            clearApprovalNotifications()
        } finally {
            super.tearDown()
        }
    }

    /** Runs a blocking call on a pooled thread and waits for it — mirrors production usage, where
     *  every [ApprovalService]/[TdtSession] call is made off the EDT. */
    private fun <T> offEdt(block: () -> T): T =
        ApplicationManager.getApplication().executeOnPooledThread<T> { block() }.get(10, TimeUnit.SECONDS)

    private fun setProfile(srv: StubServer, name: String = "p"): Profile {
        val profile = Profile(name = name, url = srv.url)
        TdtSettings.getInstance().state.profiles = mutableListOf(profile)
        TdtSettings.getInstance().state.activeProfile = name
        return profile
    }

    private fun run(id: String, ws: String = "w1") =
        Run(id = id, workspace_id = ws, command = "apply", status = "awaiting_approval", created_at = "2026-09-13T10:00:00Z")

    /** Every notification the test's fixture [project] has been shown so far, in group
     *  "Terraducktel approvals". [ApprovalNotifier.show]'s `activeProject()` resolves to this same
     *  fixture project (the only one open), so this is exactly what a user would see. */
    private fun approvalNotifications(): List<Notification> =
        NotificationsManager.getNotificationsManager()
            .getNotificationsOfType(Notification::class.java, project)
            .filter { it.groupId == "Terraducktel approvals" }

    /** Expires every notification currently in group "Terraducktel approvals" for the fixture
     *  [project] — see the class doc for why [setUp]/[tearDown] both call this. */
    private fun clearApprovalNotifications() {
        val manager = NotificationsManager.getNotificationsManager()
        for (n in approvalNotifications()) manager.expire(n)
    }

    fun testANewAwaitingRunPostsExactlyOneStickyBalloonWithThreeActionsAndASecondPollIsSilent() {
        StubServer().use { srv ->
            srv.json("GET", "/api/v1/workspaces", 200, "[]")
            var awaiting = listOf<Run>()
            srv.on("GET", "/api/v1/runs") { _, ex -> StubServer.respond(ex, 200, TdtJson.encodeToString(awaiting)) }
            srv.json("GET", "/api/v1/runs/r1/graph", 200, """{"nodes":[],"edges":[],"summary":{"add":1,"change":2,"destroy":0,"replace":0}}""")
            setProfile(srv)
            offEdt { session.reload() }
            offEdt { session.signInWithApiKey("tdt_x") }
            offEdt { service.rearm() } // primes the (empty) backlog deterministically before the
            // assertions below, rather than racing the sign-in's own asynchronous rearm.
            assertTrue("expected no balloon for the empty backlog", approvalNotifications().isEmpty())

            awaiting = listOf(run("r1"))
            offEdt { service.pollNow() }
            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()

            val posted = approvalNotifications()
            assertEquals(1, posted.size)
            val n = posted[0]
            assertEquals("Terraducktel approvals", n.groupId)
            assertEquals("Terraducktel approvals", n.title)
            assertTrue(n.content, n.content.contains("awaits approval"))
            assertTrue(n.content, n.content.contains("apply"))
            assertEquals(3, n.actions.size)
            // The counts-only-when-known rule (see ApprovalNotifier): a regression that defaulted
            // to GraphSummary()'s all-zero counts instead of the real graph would still pass every
            // OTHER assertion in this test, so pin the actual numbers here.
            assertTrue(n.content, n.content.contains("+1"))
            assertTrue(n.content, n.content.contains("~2"))

            offEdt { service.pollNow() } // r1 already seen: nothing new
            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
            assertEquals(1, approvalNotifications().size)
        }
    }

    fun testAGraphFetchFailureStillPostsABalloonWithNoCountsRatherThanMisleadingZeroes() {
        StubServer().use { srv ->
            srv.json("GET", "/api/v1/workspaces", 200, "[]")
            var awaiting = listOf<Run>()
            srv.on("GET", "/api/v1/runs") { _, ex -> StubServer.respond(ex, 200, TdtJson.encodeToString(awaiting)) }
            srv.json("GET", "/api/v1/runs/r2/graph", 500, """{"detail":"boom"}""")
            setProfile(srv)
            offEdt { session.reload() }
            offEdt { session.signInWithApiKey("tdt_x") }
            offEdt { service.rearm() }

            awaiting = listOf(run("r2"))
            offEdt { service.pollNow() }
            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()

            val posted = approvalNotifications()
            assertEquals("a failed graph fetch must not lose the notice entirely", 1, posted.size)
            assertFalse(
                "unknown counts must show as no counts, never a misleading all-zero summary: ${posted[0].content}",
                posted[0].content.contains("to add"),
            )
        }
    }

    fun testMarkSeenBeforeThePollSuppressesTheNotice() {
        StubServer().use { srv ->
            srv.json("GET", "/api/v1/workspaces", 200, "[]")
            var awaiting = listOf<Run>()
            srv.on("GET", "/api/v1/runs") { _, ex -> StubServer.respond(ex, 200, TdtJson.encodeToString(awaiting)) }
            srv.json("GET", "/api/v1/runs/r9/graph", 200, """{"nodes":[],"edges":[],"summary":{}}""")
            setProfile(srv)
            offEdt { session.reload() }
            offEdt { session.signInWithApiKey("tdt_x") }
            offEdt { service.rearm() }

            service.markSeen("r9") // e.g. the run-output tail's own toast just announced it
            awaiting = listOf(run("r9"))
            offEdt { service.pollNow() }
            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()

            assertTrue(
                "markSeen()'d run must not also get a background-poll balloon",
                approvalNotifications().isEmpty(),
            )
        }
    }

    fun testSignOutStopsTheWatcherNoFurtherRunsRequestsAcrossTwoIntervals() {
        StubServer().use { srv ->
            srv.json("GET", "/api/v1/workspaces", 200, "[]")
            srv.json("GET", "/api/v1/runs", 200, "[]")
            setProfile(srv)
            TdtSettings.getInstance().state.approvalsPollSeconds = 1 // 1000ms — floored to 15000ms
            // in production; service.minIntervalMs (50, set in setUp) keeps it at 1000ms here.
            offEdt { session.reload() }
            offEdt { session.signInWithApiKey("tdt_x") }
            // The sign-in above also fires TdtSessionListener asynchronously, but this explicit
            // call is what makes priming (and starting the loop on the 1000ms interval above)
            // deterministic before the assertions below, rather than racing a pooled-thread rearm.
            offEdt { service.rearm() }

            val callsAtSignIn = srv.calls("GET", "/api/v1/runs").size
            assertTrue("expected rearm() to have primed at least once", callsAtSignIn > 0)

            offEdt { session.signOut() } // TdtSessionListener fires -> rearm() with a null key ->
            // stop()s the loop; make it deterministic the same way.
            offEdt { service.rearm() }
            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()

            val callsAtSignOut = srv.calls("GET", "/api/v1/runs").size
            Thread.sleep(2_500) // two intervals' worth (1000ms each) plus slack
            assertEquals(
                "no /runs request should land on the old timer after signOut()",
                callsAtSignOut,
                srv.calls("GET", "/api/v1/runs").size,
            )
        }
    }
}
