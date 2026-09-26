package com.terraducktel.jetbrains

import com.intellij.ide.plugins.PluginManagerCore
import com.intellij.openapi.extensions.PluginId
import com.intellij.testFramework.fixtures.BasePlatformTestCase

class PluginLoadsTest : BasePlatformTestCase() {
    fun testDescriptorLoads() {
        val d = PluginManagerCore.getPlugin(PluginId.getId("com.terraducktel.jetbrains"))
        assertNotNull("plugin descriptor not loaded", d)
        assertEquals("Terraducktel", d!!.name)
    }
}
