package com.terraducktel.jetbrains.editor

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.components.service
import com.intellij.openapi.wm.StatusBarWidgetFactory
import com.intellij.openapi.wm.impl.status.widget.StatusBarWidgetsManager
import com.intellij.testFramework.PlatformTestUtil
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.terraducktel.jetbrains.api.TdtClient
import com.terraducktel.jetbrains.auth.PasswordSafeSecretStore
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.settings.Profile
import com.terraducktel.jetbrains.settings.TdtSettings
import com.terraducktel.jetbrains.state.Store
import com.terraducktel.jetbrains.testutil.InMemorySecretStore
import java.util.concurrent.TimeUnit

/**
 * Both status-bar widgets' whole visible surface, previously untested: [TdtStatusBarWidgetFactory]/
 * [ProfileStatusBarWidgetFactory]'s `isAvailable`, and each widget's rendered text/icon. Neuters
 * [Store]'s client provider (like [EditorStatusTest]) so `TdtSession.reload()`/`signInWithApiKey`'s
 * own `Store.refresh()` never makes a real network call — nothing here needs a live [Store].
 * [EditorStatus.view] is driven directly via [EditorStatus.setViewForTest] rather than a real
 * editor/git/workspace round trip (that pipeline is [EditorStatusTest]'s job).
 */
class StatusBarWidgetsTest : BasePlatformTestCase() {

    private val session get() = TdtSession.getInstance()
    private lateinit var secretStore: InMemorySecretStore
    private var originalClientProvider: () -> TdtClient? = { null }

    override fun setUp() {
        super.setUp()
        secretStore = InMemorySecretStore()
        session.secretStoreFactory = { secretStore }
        originalClientProvider = Store.getInstance().clientProvider
        Store.getInstance().clientProvider = { null }
    }

    override fun tearDown() {
        try {
            offEdt { session.signOut() }
            TdtSettings.getInstance().loadState(TdtSettings.State())
            session.secretStoreFactory = { PasswordSafeSecretStore() }
            Store.getInstance().clientProvider = originalClientProvider
            offEdt { session.reload() }
            EditorStatus.getInstance(project).setViewForTest(null)
            // Capture the raw Project (not `this.project`, a BasePlatformTestCase getter that NPEs
            // once THIS test instance's fixture is torn down): EditorStatus is a light service that
            // outlives this test method, and refreshWidgetAvailability()'s invokeLater can still be
            // pending when a LATER, unrelated test's own settings change finally runs it — reading
            // `this.project` at that point would crash on the long-gone fixture.
            val proj = project
            EditorStatus.getInstance(project).widgetRefresher = { factoryClass ->
                proj.service<StatusBarWidgetsManager>().updateWidget(factoryClass)
            }
            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
        } finally {
            super.tearDown()
        }
    }

    private fun <T> offEdt(block: () -> T): T =
        ApplicationManager.getApplication().executeOnPooledThread<T> { block() }.get(10, TimeUnit.SECONDS)

    // --- TdtStatusBarWidgetFactory.isAvailable() follows statusBarEnabled -----------------------

    fun testCurrentFileFactoryAvailabilityFollowsTheStatusBarEnabledSetting() {
        val factory = TdtStatusBarWidgetFactory()
        TdtSettings.getInstance().state.statusBarEnabled = true
        assertTrue(factory.isAvailable(project))

        TdtSettings.getInstance().state.statusBarEnabled = false
        assertFalse(factory.isAvailable(project))
    }

    // --- ProfileStatusBarWidgetFactory.isAvailable() follows the profile list --------------------

    fun testProfileFactoryIsUnavailableWithNoProfilesAndAvailableWithOne() {
        val factory = ProfileStatusBarWidgetFactory()
        TdtSettings.getInstance().state.profiles = mutableListOf()
        assertFalse(factory.isAvailable(project))

        TdtSettings.getInstance().state.profiles = mutableListOf(Profile(name = "p", url = "https://example.test"))
        assertTrue(factory.isAvailable(project))
    }

    // --- TdtStatusBarWidget: text/tooltip/icon follow EditorStatus.view --------------------------

    fun testCurrentFileWidgetIsBlankWhenViewIsNullAndRendersTheViewWhenSet() {
        val widget = TdtStatusBarWidget(project)
        EditorStatus.getInstance(project).setViewForTest(null)
        assertNull(widget.getSelectedValue())
        assertNull(widget.getTooltipText())
        assertNull(widget.getIcon())

        EditorStatus.getInstance(project).setViewForTest(StatusText.View("TDT: prod", "envs/prod\nClick for actions", StatusText.Severity.NONE))
        assertEquals("TDT: prod", widget.getSelectedValue())
        assertEquals("envs/prod\nClick for actions", widget.getTooltipText())
        assertNull(widget.getIcon())
    }

    fun testCurrentFileWidgetIconMapsSeverityToTheExpectedPlatformIcon() {
        val widget = TdtStatusBarWidget(project)
        val status = EditorStatus.getInstance(project)

        status.setViewForTest(StatusText.View("TDT: prod", "", StatusText.Severity.NONE))
        assertNull(widget.getIcon())

        status.setViewForTest(StatusText.View("TDT: prod", "", StatusText.Severity.WARNING))
        assertEquals(com.intellij.icons.AllIcons.General.Warning, widget.getIcon())

        status.setViewForTest(StatusText.View("TDT: prod", "", StatusText.Severity.ERROR))
        assertEquals(com.intellij.icons.AllIcons.General.Error, widget.getIcon())
    }

    // --- ProfileStatusBarWidget: "<profile> · <bu>" once signed in with a BU, else "<profile>" ----

    fun testProfileWidgetTextIsJustTheProfileNameWhenNotSignedIn() {
        TdtSettings.getInstance().state.profiles = mutableListOf(Profile(name = "acme", url = "https://example.test"))
        TdtSettings.getInstance().state.activeProfile = "acme"
        offEdt { session.reload() }

        val widget = ProfileStatusBarWidget(project)
        assertEquals("acme", widget.getSelectedValue())
    }

    fun testProfileWidgetTextIsProfileAndBuOnceSignedInWithABu() {
        TdtSettings.getInstance().state.profiles = mutableListOf(Profile(name = "acme", url = "https://example.test"))
        TdtSettings.getInstance().state.activeProfile = "acme"
        offEdt { session.reload() }
        offEdt { session.signInWithApiKey("tdt_x") }
        offEdt { session.setBu("infra") }

        val widget = ProfileStatusBarWidget(project)
        assertEquals("acme · infra", widget.getSelectedValue())
    }

    fun testProfileWidgetIsHiddenWithNoProfilesConfigured() {
        TdtSettings.getInstance().state.profiles = mutableListOf()
        val widget = ProfileStatusBarWidget(project)
        assertNull(widget.getSelectedValue())
    }

    // --- EditorStatus.refreshWidgetAvailability(): a settings change re-asks the platform ---------
    // whether each factory is available, so a freshly-added first profile (or re-enabling "Show
    // status bar item") makes its widget appear without an IDE restart — the whole point of an
    // earlier fix round. [StatusBarWidgetsManager.wasWidgetCreated] is the platform's own
    // test-facing seam for "did the manager actually (re)create this factory's widget".

    fun testASettingsChangeAsksThePlatformToRefreshBothFactoriesWithoutARestart() {
        val status = EditorStatus.getInstance(project)
        val refreshed = java.util.concurrent.CopyOnWriteArrayList<Class<out StatusBarWidgetFactory>>()
        status.widgetRefresher = { refreshed += it }

        TdtSettings.getInstance().state.profiles = mutableListOf(Profile(name = "p", url = "https://example.test"))
        TdtSettings.getInstance().fireChanged() // -> EditorStatus's TdtSettingsListener -> refreshWidgetAvailability()
        PlatformTestUtil.dispatchAllEventsInIdeEventQueue()

        assertTrue(
            "expected the current-file factory to be asked to re-evaluate availability",
            refreshed.contains(TdtStatusBarWidgetFactory::class.java),
        )
        assertTrue(
            "expected the profile factory to be asked to re-evaluate availability — a first profile" +
                " must make its widget appear without a restart",
            refreshed.contains(ProfileStatusBarWidgetFactory::class.java),
        )
        assertTrue("the profile factory's own isAvailable() must actually flip true", ProfileStatusBarWidgetFactory().isAvailable(project))
    }
}
