package com.terraducktel.jetbrains.toolwindow

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.util.Disposer
import com.intellij.openapi.wm.ToolWindowManager
import com.intellij.testFramework.PlatformTestUtil
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.intellij.ui.content.ContentFactory
import com.terraducktel.jetbrains.api.TdtClient
import com.terraducktel.jetbrains.api.Workspace
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.state.Store
import java.util.concurrent.TimeUnit

/**
 * Two different levels of the same tool window. The first test below exercises
 * [TdtToolWindowFactory.buildContents] directly — the internal seam the brief calls out for
 * exactly this situation — rather than registering a real [com.intellij.openapi.wm.ToolWindow],
 * since that pulls in window-manager machinery not worth fighting just to prove two named
 * components come out of `buildContents` correctly. It covers everything
 * [TdtToolWindowFactory.createToolWindowContent] does before registering with a real
 * [com.intellij.ui.content.ContentManager]: two named components, each a real [WorkspacesPanel] /
 * [RunsPanel] wrapped in a toolbar-carrying panel.
 *
 * [TdtToolWindowFactory.revealWorkspace], covered by the second test, is different: it starts by
 * looking the tool window up via `ToolWindowManager.getToolWindow`, so there is no exercising it
 * without a real (headless) one registered — that test does exactly that.
 */
class TdtToolWindowFactoryTest : BasePlatformTestCase() {

    private var originalClientProvider: () -> TdtClient? = { null }

    override fun setUp() {
        super.setUp()
        // TdtSession is an app-level light service whose own init{} fires a fire-and-forget
        // `reload()` on a pooled thread the FIRST time anything ever calls `getInstance()` in this
        // JVM (see WorkspacesPanel.computeRootChildren -> headMessages/notReadyMessage, both of
        // which read TdtSession.getInstance()). With no profile configured, that reload() lands on
        // `Store.getInstance().clear()` — which, landing asynchronously mid-test, would otherwise
        // race and wipe out this test's own setSnapshotForTest data. Force it to happen and settle
        // HERE, synchronously, before Store is neutered/seeded below.
        offEdt { TdtSession.getInstance().reload() }
        // Store is an app-level light service too; neuter its client provider so nothing (a stray
        // settings/session event reacting to something this test does) can trigger a REAL refresh
        // that clobbers setSnapshotForTest's data.
        originalClientProvider = Store.getInstance().clientProvider
        Store.getInstance().clientProvider = { null }
        Store.getInstance().stop()
    }

    override fun tearDown() {
        try {
            Store.getInstance().clientProvider = originalClientProvider
        } finally {
            super.tearDown()
        }
    }

    private fun <T> offEdt(block: () -> T): T =
        ApplicationManager.getApplication().executeOnPooledThread<T> { block() }.get(10, TimeUnit.SECONDS)

    fun `test buildContents produces a Workspaces and a Runs component`() {
        val factory = TdtToolWindowFactory()
        val contents = factory.buildContents(project, testRootDisposable)

        assertEquals(listOf("Workspaces", "Runs"), contents.map { it.name })
        assertTrue(contents[0].panel is WorkspacesPanel)
        assertTrue(contents[1].panel is RunsPanel)
    }

    // --- revealWorkspace(): the four-bail-out-deep glue between the factory and TreePanel --------
    // This DOES register a real ToolWindow (unlike buildContents' own test above) — revealWorkspace
    // is exactly the code that looks one up via ToolWindowManager, so there is no exercising it
    // without one.

    fun `test revealWorkspace selects a known workspace in the Workspaces tree, and an unknown id is a harmless logged no-op`() {
        val toolWindowManager = ToolWindowManager.getInstance(project)
        val toolWindow = toolWindowManager.registerToolWindow("Terraducktel") {}
        Disposer.register(testRootDisposable) { toolWindowManager.unregisterToolWindow("Terraducktel") }

        val factory = TdtToolWindowFactory()
        val contents = factory.buildContents(project, testRootDisposable)
        for (c in contents) {
            toolWindow.contentManager.addContent(ContentFactory.getInstance().createContent(c.component, c.name, false))
        }
        val workspacesPanel = contents[0].panel as WorkspacesPanel
        workspacesPanel.signedInProvider = { true } // skip the "sign in first" placeholder message

        Store.getInstance().setSnapshotForTest(
            listOf(Workspace(id = "w1", name = "prod", tf_working_dir = "envs/prod")),
            emptyList(),
        )
        // The tree only (re)builds its structure reactively, off a Store-change listener firing an
        // invokeLater'd `structureModel.invalidateAsync()` — give that a chance to run before asking
        // the async tree to find and select anything in it.
        PlatformTestUtil.dispatchAllEventsInIdeEventQueue()

        val promise = TdtToolWindowFactory.revealWorkspaceForTest(project, "w1")
        assertNotNull("expected a promise once a Workspaces content was found", promise)
        val selectedPath = PlatformTestUtil.waitForPromise(promise!!)
        assertNotNull("expected the workspace node to be found and selected", selectedPath)
        assertEquals("Workspaces", toolWindow.contentManager.selectedContent?.displayName)

        // Unknown id: TreePanel.revealWorkspace's own TreeVisitor never matches anything, so the
        // promise settles without a selection — but revealWorkspaceForTest itself must still reach
        // and return it (the tool window / Workspaces content / panel casts all still succeed),
        // rather than throwing or silently vanishing.
        val unknownPromise = TdtToolWindowFactory.revealWorkspaceForTest(project, "does-not-exist")
        assertNotNull("expected a promise even for an id that isn't in the tree", unknownPromise)
        PlatformTestUtil.waitForPromise(unknownPromise!!, 5_000)
    }
}
