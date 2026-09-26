package com.terraducktel.jetbrains.toolwindow

import com.intellij.ide.DataManager
import com.intellij.ide.impl.HeadlessDataManager
import com.intellij.ide.util.PropertiesComponent
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.util.Disposer
import com.intellij.openapi.wm.RegisterToolWindowTask
import com.intellij.openapi.wm.ToolWindowAnchor
import com.intellij.openapi.wm.ToolWindowManager
import com.intellij.testFramework.PlatformTestUtil
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.intellij.ui.content.ContentFactory
import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.api.TdtClient
import com.terraducktel.jetbrains.api.Workspace
import com.terraducktel.jetbrains.output.RunConsoles
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.state.Store
import java.awt.event.FocusEvent
import java.util.concurrent.TimeUnit

/**
 * The "Terraducktel" tool window body (one [TdtStackedPanel]: Workspaces over Runs, each a
 * collapsible section) and the bottom "Terraducktel Run" window that holds run consoles.
 * [TdtToolWindowFactory.buildPanel] is exercised directly rather than through a registered tool
 * window; [TdtToolWindowFactory.revealWorkspace] and [RunConsoles.watch] look their window up via
 * `ToolWindowManager`, so those tests register a real (headless) one.
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
        for (key in listOf(TdtStackedPanel.WORKSPACES_COLLAPSED_KEY, TdtStackedPanel.RUNS_COLLAPSED_KEY)) {
            PropertiesComponent.getInstance(project).unsetValue(key)
        }
    }

    override fun tearDown() {
        try {
            Store.getInstance().clientProvider = originalClientProvider
            Store.getInstance().setSnapshotForTest(emptyList(), emptyList())
            RunConsoles.getInstance(project).tailStarterForTest = null
        } finally {
            super.tearDown()
        }
    }

    private fun <T> offEdt(block: () -> T): T =
        ApplicationManager.getApplication().executeOnPooledThread<T> { block() }.get(10, TimeUnit.SECONDS)

    private fun awaiting(id: String) = Run(id = id, workspace_id = "w1", command = "apply", status = "awaiting_approval")

    fun `test buildPanel stacks a Workspaces section over a Runs section`() {
        val panel = TdtToolWindowFactory().buildPanel(project, testRootDisposable)

        assertEquals("Workspaces", panel.workspacesSection.title)
        assertEquals("Runs", panel.runsSection.title)
        assertTrue(panel.workspacesSection.body is WorkspacesPanel)
        assertTrue(panel.runsSection.body is RunsPanel)
        assertFalse(panel.workspacesSection.isCollapsed)
        assertFalse(panel.runsSection.isCollapsed)
    }

    fun `test collapsing a section hides its body and is remembered per project`() {
        val panel = TdtToolWindowFactory().buildPanel(project, testRootDisposable)
        panel.runsSection.setCollapsed(true)

        assertTrue(panel.runsSection.isCollapsed)
        assertFalse(panel.runsSection.body.isVisible)
        assertTrue(PropertiesComponent.getInstance(project).getBoolean(TdtStackedPanel.RUNS_COLLAPSED_KEY))

        val reopened = TdtToolWindowFactory().buildPanel(project, testRootDisposable)
        assertTrue(reopened.runsSection.isCollapsed)
        assertFalse(reopened.workspacesSection.isCollapsed)
    }

    fun `test the Runs header shows a pill with the awaiting-approval count`() {
        val panel = TdtToolWindowFactory().buildPanel(project, testRootDisposable)

        Store.getInstance().setSnapshotForTest(emptyList(), listOf(awaiting("r1"), awaiting("r2")))
        PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
        assertEquals("2", panel.runsSection.badge)

        Store.getInstance().setSnapshotForTest(emptyList(), emptyList())
        PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
        assertNull(panel.runsSection.badge)
        assertNull(panel.workspacesSection.badge)
    }

    // --- revealWorkspace(): the glue between the factory and TreePanel -------------------------

    fun `test revealWorkspace expands a collapsed Workspaces section and selects the workspace, and an unknown id is a harmless no-op`() {
        val toolWindowManager = ToolWindowManager.getInstance(project)
        val toolWindow = toolWindowManager.registerToolWindow(TdtToolWindowFactory.TOOL_WINDOW_ID) {}
        Disposer.register(testRootDisposable) { toolWindowManager.unregisterToolWindow(TdtToolWindowFactory.TOOL_WINDOW_ID) }

        val panel = TdtToolWindowFactory().buildPanel(project, testRootDisposable)
        toolWindow.contentManager.addContent(ContentFactory.getInstance().createContent(panel, null, false))
        panel.workspaces.signedInProvider = { true } // skip the "sign in first" placeholder message
        panel.workspacesSection.setCollapsed(true)

        Store.getInstance().setSnapshotForTest(
            listOf(Workspace(id = "w1", name = "prod", tf_working_dir = "envs/prod")),
            emptyList(),
        )
        // The tree only (re)builds its structure reactively, off a Store-change listener firing an
        // invokeLater'd `structureModel.invalidateAsync()` — give that a chance to run before asking
        // the async tree to find and select anything in it.
        PlatformTestUtil.dispatchAllEventsInIdeEventQueue()

        val promise = TdtToolWindowFactory.revealWorkspaceForTest(project, "w1")
        assertNotNull("expected a promise once the stacked panel was found", promise)
        assertNotNull("expected the workspace node to be found and selected", PlatformTestUtil.waitForPromise(promise!!))
        assertFalse("revealing must expand the Workspaces section", panel.workspacesSection.isCollapsed)

        val unknownPromise = TdtToolWindowFactory.revealWorkspaceForTest(project, "does-not-exist")
        assertNotNull("expected a promise even for an id that isn't in the tree", unknownPromise)
        PlatformTestUtil.waitForPromise(unknownPromise!!, 5_000)
    }

    fun `test toolbar actions see the selection of the tree that was focused last`() {
        // The headless DataManager ignores components unless told to use the production lookup.
        HeadlessDataManager.fallbackToProductionDataManager(testRootDisposable)
        val toolWindowManager = ToolWindowManager.getInstance(project)
        val toolWindow = toolWindowManager.registerToolWindow(TdtToolWindowFactory.TOOL_WINDOW_ID) {}
        Disposer.register(testRootDisposable) { toolWindowManager.unregisterToolWindow(TdtToolWindowFactory.TOOL_WINDOW_ID) }
        val panel = TdtToolWindowFactory().buildPanel(project, testRootDisposable)
        toolWindow.contentManager.addContent(ContentFactory.getInstance().createContent(panel, null, false))
        panel.workspaces.signedInProvider = { true }
        Store.getInstance().setSnapshotForTest(listOf(Workspace(id = "w1", name = "prod", tf_working_dir = "envs/prod")), emptyList())
        PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
        PlatformTestUtil.waitForPromise(TdtToolWindowFactory.revealWorkspaceForTest(project, "w1")!!)

        fun focus(tree: TreePanel) = tree.treeComponent.focusListeners.forEach { it.focusGained(FocusEvent(tree.treeComponent, FocusEvent.FOCUS_GAINED)) }
        fun selectedWorkspace() = DataManager.getInstance().getDataContext(panel).getData(TdtDataKeys.WORKSPACE)

        focus(panel.runs)
        assertNull("the Runs tree has no selection, so nothing may leak from the Workspaces tree", selectedWorkspace())
        focus(panel.workspaces)
        assertEquals("w1", selectedWorkspace()?.id)
    }

    // --- Terraducktel Run: the bottom tool window for run consoles ------------------------------

    fun `test watching a run opens one closeable console tab in Terraducktel Run, reused on a second watch`() {
        val toolWindowManager = ToolWindowManager.getInstance(project)
        val runWindow = toolWindowManager.registerToolWindow(
            RegisterToolWindowTask(id = RunConsoles.TOOL_WINDOW_ID, anchor = ToolWindowAnchor.BOTTOM, canCloseContent = true),
        )
        Disposer.register(testRootDisposable) { toolWindowManager.unregisterToolWindow(RunConsoles.TOOL_WINDOW_ID) }
        val started = mutableListOf<String>()
        val consoles = RunConsoles.getInstance(project)
        consoles.tailStarterForTest = { runId -> started += runId }

        consoles.watch("abcdef1234", "vpc")
        consoles.watch("abcdef1234", "vpc") // still following: just reveals the existing tab

        val contents = runWindow.contentManager.contents
        assertEquals(1, contents.size)
        assertEquals("Run abcdef12 · vpc", contents[0].displayName)
        assertTrue(contents[0].isCloseable)
        assertEquals(listOf("abcdef1234"), started)
        assertEquals("Terraducktel Run", RunConsoles.TOOL_WINDOW_ID)
    }

    fun `test plugin xml registers Terraducktel Run as a bottom tool window`() {
        val xml = javaClass.getResource("/META-INF/plugin.xml")!!.readText()
        val tag = Regex("""<toolWindow id="Terraducktel Run"[^>]*/>""").find(xml)?.value
        assertNotNull("no Terraducktel Run toolWindow in plugin.xml", tag)
        assertTrue(tag!!, tag.contains("""anchor="bottom""""))
        assertTrue(tag, tag.contains("""canCloseContents="true""""))
    }
}
