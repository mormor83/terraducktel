package com.terraducktel.jetbrains.toolwindow

import com.intellij.testFramework.fixtures.BasePlatformTestCase

/**
 * Registering a real [com.intellij.openapi.wm.ToolWindow] in a headless platform test (via
 * `ToolWindowManager.registerToolWindow`) pulls in window-manager machinery that isn't worth
 * fighting for a unit test — so this exercises [TdtToolWindowFactory.buildContents] directly,
 * the internal seam the brief calls out for exactly this situation. It covers everything
 * [TdtToolWindowFactory.createToolWindowContent] does before registering with a real
 * [com.intellij.ui.content.ContentManager]: two named components, each a real [WorkspacesPanel] /
 * [RunsPanel] wrapped in a toolbar-carrying panel.
 */
class TdtToolWindowFactoryTest : BasePlatformTestCase() {

    fun `test buildContents produces a Workspaces and a Runs component`() {
        val factory = TdtToolWindowFactory()
        val contents = factory.buildContents(project, testRootDisposable)

        assertEquals(listOf("Workspaces", "Runs"), contents.map { it.name })
        assertTrue(contents[0].panel is WorkspacesPanel)
        assertTrue(contents[1].panel is RunsPanel)
    }
}
