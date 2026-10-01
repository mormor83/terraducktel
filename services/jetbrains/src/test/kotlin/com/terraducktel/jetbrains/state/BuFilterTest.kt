package com.terraducktel.jetbrains.state

import com.intellij.util.xmlb.XmlSerializer
import com.terraducktel.jetbrains.api.BusinessUnit
import com.terraducktel.jetbrains.settings.TdtSettings
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Business-unit filter rules: persisted per profile as a list of HIDDEN slugs (so a BU that shows
 *  up later is visible by default), and an empty selection is rejected. */
class BuFilterTest {
    private val infra = BusinessUnit("1", "infra", "Infra")
    private val apps = BusinessUnit("2", "apps", "Apps")
    private val data = BusinessUnit("3", "data", "Data")

    @Test
    fun `nothing hidden means every business unit is visible`() {
        assertEquals(listOf(infra, apps, data), BuFilter.visible(listOf(infra, apps, data), emptySet()))
    }

    @Test
    fun `hidden slugs are filtered out and a business unit the user never hid is visible by default`() {
        val hidden = setOf("apps")
        assertEquals(listOf(infra), BuFilter.visible(listOf(infra, apps), hidden))
        // "data" was never seen before (not in the hidden set), so it shows up on its own
        assertEquals(listOf(infra, data), BuFilter.visible(listOf(infra, apps, data), hidden))
    }

    @Test
    fun `a hidden slug that is no longer in the list is simply ignored`() {
        assertEquals(listOf(infra), BuFilter.visible(listOf(infra), setOf("gone")))
    }

    @Test
    fun `selecting some business units hides the rest`() {
        assertEquals(setOf("apps", "data"), BuFilter.hiddenForSelection(listOf(infra, apps, data), setOf("infra")))
    }

    @Test
    fun `selecting every business unit clears the hidden set`() {
        assertEquals(emptySet<String>(), BuFilter.hiddenForSelection(listOf(infra, apps), setOf("infra", "apps")))
    }

    @Test
    fun `selecting zero business units is rejected`() {
        assertNull(BuFilter.hiddenForSelection(listOf(infra, apps), emptySet()))
        // slugs that are not in the list do not count as a selection either
        assertNull(BuFilter.hiddenForSelection(listOf(infra, apps), setOf("gone")))
    }

    @Test
    fun `hidden slugs are stored per profile`() {
        val settings = TdtSettings()
        assertEquals(emptySet<String>(), settings.hiddenBuSlugs("prod"))

        settings.setHiddenBuSlugs("prod", setOf("apps", "data"))
        settings.setHiddenBuSlugs("staging", setOf("infra"))

        assertEquals(setOf("apps", "data"), settings.hiddenBuSlugs("prod"))
        assertEquals(setOf("infra"), settings.hiddenBuSlugs("staging"))
        assertEquals(emptySet<String>(), settings.hiddenBuSlugs("other"))
    }

    @Test
    fun `clearing the hidden set for a profile removes its entry`() {
        val settings = TdtSettings()
        settings.setHiddenBuSlugs("prod", setOf("apps"))
        settings.setHiddenBuSlugs("prod", emptySet())
        assertEquals(emptySet<String>(), settings.hiddenBuSlugs("prod"))
        assertTrue(settings.state.buHiddenByProfile.isEmpty())
    }

    @Test
    fun `hidden slugs survive an XML round trip`() {
        val source = TdtSettings()
        source.setHiddenBuSlugs("prod", setOf("apps", "data"))

        val roundTripped = XmlSerializer.deserialize(XmlSerializer.serialize(source.state), TdtSettings.State::class.java)
        val target = TdtSettings()
        target.loadState(roundTripped)

        assertEquals(setOf("apps", "data"), target.hiddenBuSlugs("prod"))
    }
}
