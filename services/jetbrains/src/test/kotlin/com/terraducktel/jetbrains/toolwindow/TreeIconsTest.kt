package com.terraducktel.jetbrains.toolwindow

import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.intellij.ui.AnimatedIcon

class TreeIconsTest : BasePlatformTestCase() {

    fun `test settled statuses map to the handoff's brand icon set`() {
        val expected = mapOf(
            "applied" to "applied", "success" to "applied", "planned" to "planned",
            "awaiting_approval" to "awaiting", "failed" to "failed", "cancelled" to "cancelled",
            "skipped" to "cancelled", "pending" to "pending", null to "none", "quantum" to "none",
        )
        for ((status, asset) in expected) assertEquals("$status", "/icons/status/$asset.svg", TreeIcons.statusIconPath(status))
    }

    fun `test in-flight statuses have no static icon and use the platform spinner`() {
        for (status in listOf("running", "planning", "applying")) {
            assertNull(TreeIcons.statusIconPath(status))
            assertSame(AnimatedIcon.Default.INSTANCE, TreeIcons.runStatusIcon(status))
            assertSame(AnimatedIcon.Default.INSTANCE, TreeIcons.stepStatusIcon(status))
        }
    }

    fun `test every status and cloud icon ships a light and a dark variant`() {
        val paths = listOf("applied", "planned", "awaiting", "failed", "cancelled", "pending", "none").map { "/icons/status/$it.svg" } +
            listOf("aws", "azure", "gcp", "other").map { TreeIcons.cloudIconPath(it) }
        for (p in paths) {
            assertNotNull(p, TreeIcons::class.java.getResource(p))
            assertNotNull(p, TreeIcons::class.java.getResource(p.replace(".svg", "_dark.svg")))
        }
        assertEquals("/icons/cloud/other.svg", TreeIcons.cloudIconPath("something-new"))
    }
}
