package com.terraducktel.jetbrains.settings

import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.intellij.util.xmlb.XmlSerializer
import com.terraducktel.jetbrains.auth.PasswordSafeSecretStore

/**
 * [TdtSettings] state round-trip / lookup behaviour, plus a smoke test for [TdtConfigurable].
 * `TdtSettings` is an app-level service, so it persists across tests within one JVM — [tearDown]
 * restores it to fresh defaults so tests can't leak into each other.
 */
class TdtSettingsTest : BasePlatformTestCase() {

    override fun tearDown() {
        try {
            TdtSettings.getInstance().loadState(TdtSettings.State())
            val secrets = PasswordSafeSecretStore()
            secrets.delete("terraducktel.cred.prod")
            secrets.delete("terraducktel.cred.prod-renamed")
        } finally {
            super.tearDown()
        }
    }

    private fun twoProfileState(): TdtSettings.State = TdtSettings.State().apply {
        profiles = mutableListOf(
            Profile(name = "prod", url = "https://tdt.example.com", uiUrl = "", insecureTls = false),
            Profile(name = "staging", url = "https://staging.tdt.example.com", uiUrl = "https://ui.staging.tdt.example.com", insecureTls = true),
        )
        activeProfile = "staging"
        buByProfile = mutableMapOf("prod" to "platform", "staging" to "platform")
        refreshIntervalSeconds = 45
        runsLimit = 500
        approvalsPollSeconds = 15
        trace = true
        statusBarEnabled = false
        notifiedRuns = mutableMapOf("run-1" to 123456789L)
    }

    fun testStateRoundTripsThroughXmlSerializerAndLoadState() {
        val source = TdtSettings()
        source.loadState(twoProfileState())

        // A real XmlSerializer round trip (serialize to XML, deserialize back), THEN the
        // component's own loadState (XmlSerializerUtil.copyBean) into a second fresh instance —
        // exercising both the wire format and the bean-copy plumbing `loadState` relies on.
        val element = XmlSerializer.serialize(source.state)
        val roundTripped = XmlSerializer.deserialize(element, TdtSettings.State::class.java)

        val target = TdtSettings()
        target.loadState(roundTripped)

        assertEquals(source.state.profiles, target.state.profiles)
        assertEquals(source.state.activeProfile, target.state.activeProfile)
        assertEquals(source.state.buByProfile, target.state.buByProfile)
        assertEquals(source.state.refreshIntervalSeconds, target.state.refreshIntervalSeconds)
        assertEquals(source.state.runsLimit, target.state.runsLimit)
        assertEquals(source.state.approvalsPollSeconds, target.state.approvalsPollSeconds)
        assertEquals(source.state.trace, target.state.trace)
        assertEquals(source.state.statusBarEnabled, target.state.statusBarEnabled)
        assertEquals(source.state.notifiedRuns, target.state.notifiedRuns)
    }

    fun testDefaults() {
        val defaults = TdtSettings().state
        assertEquals(30, defaults.refreshIntervalSeconds)
        assertEquals(200, defaults.runsLimit)
        assertEquals(60, defaults.approvalsPollSeconds)
        assertFalse(defaults.trace)
        assertTrue(defaults.statusBarEnabled)
        assertTrue(defaults.profiles.isEmpty())
        assertEquals("", defaults.activeProfile)
    }

    fun testActiveProfileReturnsNamedProfileWhenItExists() {
        val settings = TdtSettings()
        settings.loadState(twoProfileState())
        assertEquals("staging", settings.activeProfile()?.name)
    }

    fun testActiveProfileFallsBackToFirstByNameWhenActiveNameIsUnknown() {
        val settings = TdtSettings()
        settings.loadState(TdtSettings.State().apply {
            profiles = mutableListOf(Profile(name = "zebra", url = "https://z"), Profile(name = "apple", url = "https://a"))
            activeProfile = "does-not-exist"
        })
        assertEquals("apple", settings.activeProfile()?.name)
    }

    fun testActiveProfileIsNullWhenThereAreNoProfiles() {
        assertNull(TdtSettings().activeProfile())
    }

    fun testProfileLookupByName() {
        val settings = TdtSettings()
        settings.loadState(twoProfileState())
        assertNotNull(settings.profile("prod"))
        assertNull(settings.profile("does-not-exist"))
    }

    fun testUiUrlForPrefersUiUrlAndStripsTrailingSlash() {
        val settings = TdtSettings()
        assertEquals(
            "https://ui.example.com",
            settings.uiUrlFor(Profile(name = "x", url = "https://api.example.com/", uiUrl = "https://ui.example.com/")),
        )
        assertEquals(
            "https://api.example.com",
            settings.uiUrlFor(Profile(name = "x", url = "https://api.example.com/", uiUrl = "")),
        )
    }

    fun testConfigurablePanelBuildsResetIsNotModifiedThenApplyPersists() {
        TdtSettings.getInstance().loadState(TdtSettings.State())

        val configurable = TdtConfigurable()
        configurable.createPanel() // must not throw
        configurable.reset()
        assertFalse(configurable.isModified())

        // Simulate a user edit through the actual widget — apply() pulls the DSL-bound scalar
        // fields FROM the component INTO `working`, so mutating `working` directly would just be
        // clobbered by the (unchanged) widget value on the next apply().
        configurable.refreshIntervalField.text = "99"
        assertTrue(configurable.isModified())

        configurable.apply()
        assertEquals(99, TdtSettings.getInstance().state.refreshIntervalSeconds)
        assertFalse(configurable.isModified())
    }

    fun testEditingAProfileFieldInPlaceIsDetectedAsModifiedAndApplyPersistsIt() {
        TdtSettings.getInstance().loadState(TdtSettings.State().apply {
            profiles = mutableListOf(Profile(name = "prod", url = "https://tdt.example.com"))
            activeProfile = "prod"
        })
        val configurable = TdtConfigurable()
        configurable.createPanel()
        configurable.reset()
        assertFalse(configurable.isModified())

        // The table edits a CLONE of the profile, not the same object the baseline holds — if it
        // aliased the baseline, this in-place edit would be invisible to isModified().
        configurable.profiles.first { it.name == "prod" }.url = "https://tdt2.example.com"
        assertTrue(configurable.isModified())

        configurable.apply()
        assertEquals("https://tdt2.example.com", TdtSettings.getInstance().profile("prod")?.url)
        assertFalse(configurable.isModified())
    }

    fun testRemovingAProfileDeletesItsCredentialAndBuMapping() {
        TdtSettings.getInstance().loadState(TdtSettings.State().apply {
            profiles = mutableListOf(Profile(name = "prod", url = "https://tdt.example.com"), Profile(name = "staging", url = "https://staging.example.com"))
            activeProfile = "prod"
            buByProfile = mutableMapOf("prod" to "platform", "staging" to "platform")
        })
        PasswordSafeSecretStore().set("terraducktel.cred.prod", "s3cr3t")

        val configurable = TdtConfigurable()
        configurable.createPanel()
        configurable.reset()

        configurable.removeProfileForTest("prod")
        configurable.apply()

        assertNull(PasswordSafeSecretStore().get("terraducktel.cred.prod"))
        assertFalse(TdtSettings.getInstance().state.buByProfile.containsKey("prod"))
        // The removed profile WAS the active one, so activeProfile must be cleared, not left
        // dangling on a name that no longer exists.
        assertEquals("", TdtSettings.getInstance().state.activeProfile)
    }

    fun testRenamingAProfileInPlaceDoesNotDeleteItsCredentialOrBuMapping() {
        TdtSettings.getInstance().loadState(TdtSettings.State().apply {
            profiles = mutableListOf(Profile(name = "prod", url = "https://tdt.example.com"), Profile(name = "staging", url = "https://staging.example.com"))
            activeProfile = "staging" // rename a profile that ISN'T the active one
            buByProfile = mutableMapOf("prod" to "platform")
        })
        PasswordSafeSecretStore().set("terraducktel.cred.prod", "s3cr3t")

        val configurable = TdtConfigurable()
        configurable.createPanel()
        configurable.reset()

        // Mutating the Profile bean's `.name` field in place mirrors what an in-table cell edit
        // does (same object, same row) — it must NOT be mistaken for a removal.
        configurable.profiles.first { it.name == "prod" }.name = "prod-renamed"
        configurable.apply()

        assertEquals("s3cr3t", PasswordSafeSecretStore().get("terraducktel.cred.prod"))
        assertTrue(TdtSettings.getInstance().state.buByProfile.containsKey("prod"))
        assertNotNull(TdtSettings.getInstance().profile("prod-renamed"))
    }
}
