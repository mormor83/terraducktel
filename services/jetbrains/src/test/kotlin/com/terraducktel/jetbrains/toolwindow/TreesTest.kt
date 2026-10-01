package com.terraducktel.jetbrains.toolwindow

import com.intellij.icons.AllIcons
import com.intellij.openapi.application.ApplicationManager
import com.intellij.testFramework.PlatformTestUtil
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.intellij.ui.tree.TreeVisitor
import com.terraducktel.jetbrains.api.BusinessUnit
import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.api.RunStep
import com.terraducktel.jetbrains.api.Workspace
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.settings.Profile
import com.terraducktel.jetbrains.settings.TdtSettings
import com.terraducktel.jetbrains.state.BuState
import com.terraducktel.jetbrains.state.Store
import com.terraducktel.jetbrains.testutil.StubServer
import com.terraducktel.jetbrains.toolwindow.nodes.BuNode
import com.terraducktel.jetbrains.toolwindow.nodes.CloudGroupNode
import com.terraducktel.jetbrains.toolwindow.nodes.FilterHeaderNode
import com.terraducktel.jetbrains.toolwindow.nodes.FolderTreeNode
import com.terraducktel.jetbrains.toolwindow.nodes.MessageNode
import com.terraducktel.jetbrains.toolwindow.nodes.RegionNode
import com.terraducktel.jetbrains.toolwindow.nodes.RunNode
import com.terraducktel.jetbrains.toolwindow.nodes.StepNode
import com.terraducktel.jetbrains.toolwindow.nodes.TdtNode
import com.terraducktel.jetbrains.toolwindow.nodes.WorkspaceNode
import java.util.concurrent.TimeUnit

/**
 * Platform tests for Task 9's Workspaces/Runs trees: [WorkspacesPanel]/[RunsPanel] built on top
 * of the shared [TreePanel] plumbing. Most tests use [Store.setSnapshotForTest] to seed a fixed
 * snapshot and the [TreePanel.signedInProvider] / [TreePanel.profileConfiguredProvider] test
 * seams to exercise the "not usable yet" branch deterministically, without a real sign-in round
 * trip; [TreePanel.rebuild] computes the root's children synchronously (no async tree machinery),
 * which is all those need. The step-cache tests below are the exception — they sign in for real
 * against a [StubServer] (the same pattern `TdtSessionTest` uses), because [com.terraducktel.
 * jetbrains.toolwindow.nodes.RunNode]'s step fetch reads [TdtSession] directly. Neither this file
 * nor `TdtToolWindowFactoryTest` drives the real `StructureTreeModel`/`AsyncTreeModel` pipeline
 * end-to-end (that would need a registered `ToolWindow` and pumping the async invoker/EDT queue);
 * `rebuild()` and the pure [TreePanel.revealAction] are believed to cover the same logic
 * deterministically instead.
 */
class TreesTest : BasePlatformTestCase() {

    private lateinit var originalHiddenProvider: () -> Set<String>

    override fun setUp() {
        super.setUp()
        originalHiddenProvider = Store.getInstance().hiddenProvider
    }

    override fun tearDown() {
        try {
            offEdt { TdtSession.getInstance().signOut() }
            TdtSettings.getInstance().loadState(TdtSettings.State())
            Store.getInstance().clientProvider = { TdtSession.getInstance().clientOrNull() }
            offEdt { TdtSession.getInstance().reload() }
            // Both signOut() and reload() publish sessionChanged() via invokeLater — drain it now
            // so it isn't left sitting on the EDT queue for a later test's listener to pick up.
            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
            Store.getInstance().setSnapshotForTest(emptyList(), emptyList())
            Store.getInstance().hiddenProvider = originalHiddenProvider
            Store.getInstance().setLastErrorForTest(null)
            // RunNode's step cache is a static, process-wide companion (shared by every RunNode
            // instance, not scoped to a panel or test) — clear it directly rather than relying on
            // some still-alive panel's sessionChanged listener to have done it.
            RunNode.clearAll()
        } finally {
            super.tearDown()
        }
    }

    private fun ws(
        id: String,
        name: String = id,
        awsAccountId: String? = "000000000000",
        region: String = "us-east-1",
        tfWorkingDir: String = "",
        driftStatus: String = "unknown",
        repoRef: String = "main",
    ): Workspace = Workspace(
        id = id,
        business_unit_id = "bu",
        name = name,
        environment = "dev",
        aws_account_id = awsAccountId,
        region = region,
        tf_working_dir = tfWorkingDir,
        repo_ref = repoRef,
        drift_status = driftStatus,
    )

    private fun run(id: String, wsId: String, status: String, createdAt: String? = null, command: String = "plan"): Run =
        Run(id = id, workspace_id = wsId, command = command, status = status, created_at = createdAt)

    /** Reads a node's rendered label back out — [MessageNode] uses plain `presentableText`, every
     *  other node builds it from colored fragments via `addText`. */
    private fun TdtNode.renderedText(): String {
        update()
        val p = presentation
        val colored = p.coloredText.joinToString("") { it.text }
        return colored.ifEmpty { p.presentableText ?: "" }
    }

    private fun offEdt(block: () -> Unit) {
        ApplicationManager.getApplication().executeOnPooledThread { block() }.get(10, TimeUnit.SECONDS)
    }

    private fun setProfile(srv: StubServer, name: String = "p"): Profile {
        val profile = Profile(name = name, url = srv.url)
        TdtSettings.getInstance().state.profiles = mutableListOf(profile)
        TdtSettings.getInstance().state.activeProfile = name
        return profile
    }

    /** Polls [condition] until true or [timeoutMs] elapses (failing the test on timeout) — used
     *  only to wait for a [RunNode] step fetch that runs on a pooled thread. */
    private fun awaitCondition(timeoutMs: Long = 5_000, intervalMs: Long = 20, condition: () -> Boolean) {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            if (condition()) return
            Thread.sleep(intervalMs)
        }
        fail("condition not met within ${timeoutMs}ms")
    }

    // ─── helpers for the business-unit tree ──────────────────────────────────────

    private val infra = BusinessUnit("id-infra", "infra", "Infra")
    private val apps = BusinessUnit("id-apps", "apps", "Apps")
    private val data = BusinessUnit("id-data", "data", "Data")

    private fun state(bu: BusinessUnit, workspaces: List<Workspace> = emptyList(), runs: List<Run> = emptyList(), error: String? = null, loaded: Boolean = true) =
        BuState(bu, workspaces, runs, error, loaded)

    private fun workspacesPanel(): WorkspacesPanel =
        WorkspacesPanel(project, testRootDisposable).also { it.signedInProvider = { true } }

    private fun runsPanel(): RunsPanel =
        RunsPanel(project, testRootDisposable).also { it.signedInProvider = { true } }

    private fun RunsPanel.runNodes(): List<RunNode> =
        rebuild().filterIsInstance<BuNode>().flatMap { it.buildChildren() }.filterIsInstance<RunNode>()

    // ─── WorkspacesPanel ───────────────────────────────────────────────────────

    fun `test workspaces tree roots are the visible business units sorted by name`() {
        Store.getInstance().setSnapshotForTest(
            listOf(state(infra, listOf(ws("w1"))), state(data, listOf(ws("w2"))), state(apps, listOf(ws("w3")))),
        )

        val roots = workspacesPanel().rebuild()

        assertEquals(listOf("Apps", "Data", "Infra"), roots.filterIsInstance<BuNode>().map { it.bu.name })
        assertTrue(roots.all { it is BuNode })
    }

    fun `test a business unit row shows its name, slug with workspace count, and a business unit icon`() {
        Store.getInstance().setSnapshotForTest(listOf(state(infra, listOf(ws("w1"), ws("w2"))), state(apps)))

        val roots = workspacesPanel().rebuild().filterIsInstance<BuNode>()
        val infraRow = roots.single { it.bu.slug == "infra" }
        infraRow.update()

        assertEquals("Infra  infra · 2 workspaces", infraRow.renderedText())
        assertSame(AllIcons.Nodes.Module, infraRow.presentation.getIcon(false))
    }

    fun `test a business unit's children use the existing grouping over that business unit's workspaces only`() {
        val awsWs = ws(
            id = "w1", name = "vpc", awsAccountId = "123456789012", region = "eu-west-1",
            tfWorkingDir = "account-123456789012/eu-west-1/vpc",
        )
        val cfWs = ws(
            id = "w2", name = "dns", awsAccountId = null, region = "",
            tfWorkingDir = "cloudflare/tenant-home/dns",
        )
        val otherBuWs = ws(id = "w3", name = "elsewhere", awsAccountId = "999999999999", region = "us-east-1", tfWorkingDir = "x/y/z")
        Store.getInstance().setSnapshotForTest(listOf(state(infra, listOf(awsWs, cfWs)), state(apps, listOf(otherBuWs))))

        val infraRow = workspacesPanel().rebuild().filterIsInstance<BuNode>().single { it.bu.slug == "infra" }
        val groups = infraRow.buildChildren()
        assertEquals(2, groups.size)

        val awsGroup = groups.filterIsInstance<CloudGroupNode>().single { it.group.cloud.name == "AWS" }
        val cfGroup = groups.filterIsInstance<CloudGroupNode>().single { it.group.label == "cloudflare" }
        assertEquals("infra", awsGroup.bu)
        assertEquals("infra/cloud:AWS:123456789012", awsGroup.id)

        val awsRegions = awsGroup.buildChildren().filterIsInstance<RegionNode>()
        assertEquals(1, awsRegions.size)
        assertEquals("eu-west-1", awsRegions.single().region.region)

        val awsLeaves = awsRegions.single().buildChildren().filterIsInstance<WorkspaceNode>()
        assertEquals(1, awsLeaves.size)
        assertEquals("vpc", awsLeaves.single().leaf)
        assertEquals("w1", awsLeaves.single().ws.id)
        assertEquals("every workspace node carries its business unit", "infra", awsLeaves.single().bu)

        // Descend cloudflare's region → "cloudflare" folder → "tenant-home" folder → the "dns"
        // workspace leaf (Grouping doesn't strip a non-AWS/Azure/GCP top path segment, so it
        // reappears as a folder under its own cloud group — this asserts that real, existing
        // shape rather than a simplified one).
        val cfRegions = cfGroup.buildChildren().filterIsInstance<RegionNode>()
        assertEquals(1, cfRegions.size)
        val topFolders = cfRegions.single().buildChildren().filterIsInstance<FolderTreeNode>()
        val cloudflareFolder = topFolders.single { it.folder.name == "cloudflare" }
        assertEquals("infra", cloudflareFolder.bu)
        val tenantHomeFolder = cloudflareFolder.buildChildren().filterIsInstance<FolderTreeNode>().single { it.folder.name == "tenant-home" }
        val dnsLeaf = tenantHomeFolder.buildChildren().filterIsInstance<WorkspaceNode>().single()
        assertEquals("dns", dnsLeaf.leaf)
        assertEquals("w2", dnsLeaf.ws.id)
        assertEquals("infra", dnsLeaf.bu)

        // the other business unit's workspace never leaks into this one
        val appsRow = workspacesPanel().rebuild().filterIsInstance<BuNode>().single { it.bu.slug == "apps" }
        assertEquals(1, appsRow.buildChildren().size)
        assertEquals("apps", (appsRow.buildChildren().single() as CloudGroupNode).bu)
    }

    fun `test the same cloud key in two business units yields distinct node ids`() {
        val a = ws(id = "w1", awsAccountId = "123456789012", region = "eu-west-1", tfWorkingDir = "account-123456789012/eu-west-1/a")
        val b = ws(id = "w2", awsAccountId = "123456789012", region = "eu-west-1", tfWorkingDir = "account-123456789012/eu-west-1/b")
        Store.getInstance().setSnapshotForTest(listOf(state(infra, listOf(a)), state(apps, listOf(b))))

        val ids = workspacesPanel().rebuild().filterIsInstance<BuNode>().flatMap { it.buildChildren() }.map { it.id }

        assertEquals(2, ids.toSet().size)
    }

    fun `test an empty business unit shows a single No workspaces message`() {
        Store.getInstance().setSnapshotForTest(listOf(state(infra)))

        val children = workspacesPanel().rebuild().filterIsInstance<BuNode>().single().buildChildren()

        assertEquals(1, children.size)
        assertEquals("No workspaces", (children.single() as MessageNode).renderedText())
    }

    fun `test a business unit that has not loaded yet shows a loading message`() {
        Store.getInstance().setSnapshotForTest(listOf(state(infra, loaded = false)))

        val children = workspacesPanel().rebuild().filterIsInstance<BuNode>().single().buildChildren()

        assertEquals("Loading…", (children.single() as MessageNode).renderedText())
    }

    fun `test a business unit with an error shows the error message and keeps its last good data`() {
        Store.getInstance().setSnapshotForTest(
            listOf(state(infra, listOf(ws("w1", tfWorkingDir = "a/b/c")), error = "boom"), state(apps, listOf(ws("w2")))),
        )

        val roots = workspacesPanel().rebuild().filterIsInstance<BuNode>()
        val infraRow = roots.single { it.bu.slug == "infra" }
        infraRow.update()
        val children = infraRow.buildChildren()

        assertEquals("Last refresh failed: boom", (children.first() as MessageNode).renderedText())
        assertTrue(children.drop(1).any { it is CloudGroupNode })
        assertEquals("Infra  infra · error", infraRow.renderedText())
        // the healthy business unit is unaffected
        assertTrue(roots.single { it.bu.slug == "apps" }.buildChildren().none { it is MessageNode })
    }

    fun `test a filter header row shows when some business units are hidden and is absent otherwise`() {
        Store.getInstance().setSnapshotForTest(listOf(state(infra), state(apps), state(data)))
        val panel = workspacesPanel()
        assertTrue(panel.rebuild().none { it is FilterHeaderNode })

        Store.getInstance().hiddenProvider = { setOf("apps", "data") }
        val roots = panel.rebuild()

        val header = roots.first() as FilterHeaderNode
        assertEquals(1, header.shown)
        assertEquals(3, header.total)
        assertEquals("Showing 1 of 3 business units — Filter…", header.renderedText())
        assertEquals(listOf("infra"), roots.filterIsInstance<BuNode>().map { it.bu.slug })
    }

    fun `test a single visible business unit is expanded by default and several are collapsed`() {
        Store.getInstance().setSnapshotForTest(listOf(state(infra), state(apps)))
        val panel = workspacesPanel()
        assertTrue(panel.rebuild().filterIsInstance<BuNode>().none { it.autoExpand })

        Store.getInstance().hiddenProvider = { setOf("apps") }
        assertTrue(panel.rebuild().filterIsInstance<BuNode>().single().autoExpand)
    }

    fun `test signed out workspaces tree renders a single sign in message`() {
        val panel = WorkspacesPanel(project, testRootDisposable)
        panel.signedInProvider = { false }
        panel.profileConfiguredProvider = { true }

        val roots = panel.rebuild()
        assertEquals(1, roots.size)
        val message = roots.single() as MessageNode
        assertEquals("Sign in to Terraducktel", message.renderedText())
    }

    fun `test last refresh error renders a warning message before the tree content`() {
        Store.getInstance().setSnapshotForTest(listOf(state(infra, listOf(ws("w1")))))
        Store.getInstance().setLastErrorForTest(RuntimeException("boom"))

        val roots = workspacesPanel().rebuild()
        assertTrue(roots.isNotEmpty())
        val first = roots.first() as MessageNode
        assertEquals("Last refresh failed: boom", first.renderedText())
        assertTrue(roots.drop(1).any { it is BuNode })
    }

    // ─── RunsPanel ─────────────────────────────────────────────────────────────

    fun `test runs panel orders awaiting approval before running before applied, newest first, and counts pending approvals`() {
        val theWs = ws(id = "w1", name = "vpc")
        val applied = run(id = "r-applied", wsId = "w1", status = "applied", createdAt = "2024-01-01T00:00:00Z")
        val running = run(id = "r-running", wsId = "w1", status = "running", createdAt = "2024-01-02T00:00:00Z")
        val awaiting = run(id = "r-awaiting", wsId = "w1", status = "awaiting_approval", createdAt = "2024-01-03T00:00:00Z")
        // A second, older awaiting_approval run to also verify "newest first within a status".
        val awaitingOlder = run(id = "r-awaiting-2", wsId = "w1", status = "awaiting_approval", createdAt = "2023-01-01T00:00:00Z")
        Store.getInstance().setSnapshotForTest(listOf(theWs), listOf(applied, running, awaiting, awaitingOlder))

        val panel = runsPanel()

        val runNodes = panel.runNodes()
        assertEquals(
            listOf("r-awaiting", "r-awaiting-2", "r-running", "r-applied"),
            runNodes.map { it.run.id },
        )
        assertEquals(2, panel.pendingApprovals())
    }

    fun `test runs panel groups runs under their business unit and every run node carries it`() {
        Store.getInstance().setSnapshotForTest(
            listOf(
                state(infra, listOf(ws("w1")), listOf(run("r1", "w1", "applied"), run("r2", "w1", "awaiting_approval"))),
                state(apps, listOf(ws("w2")), listOf(run("r3", "w2", "running"))),
            ),
        )
        val panel = runsPanel()

        val roots = panel.rebuild().filterIsInstance<BuNode>()

        assertEquals(listOf("Apps", "Infra"), roots.map { it.bu.name })
        assertEquals(listOf("r3"), roots[0].buildChildren().filterIsInstance<RunNode>().map { it.run.id })
        assertEquals(listOf("r2", "r1"), roots[1].buildChildren().filterIsInstance<RunNode>().map { it.run.id })
        assertEquals(listOf("apps"), roots[0].buildChildren().filterIsInstance<RunNode>().map { it.bu })
        assertEquals(listOf("infra", "infra"), roots[1].buildChildren().filterIsInstance<RunNode>().map { it.bu })
        assertEquals(1, panel.pendingApprovals())
        roots[1].update()
        assertEquals("Infra  infra · 2 runs", roots[1].renderedText())
    }

    fun `test runs panel business units without runs show No recent runs`() {
        Store.getInstance().setSnapshotForTest(listOf(state(infra), state(apps, runs = listOf(run("r1", "w1", "applied")))))

        val roots = runsPanel().rebuild().filterIsInstance<BuNode>()

        val emptyChildren = roots.single { it.bu.slug == "infra" }.buildChildren()
        assertEquals("No recent runs", (emptyChildren.single() as MessageNode).renderedText())
    }

    fun `test runs panel hides runs of hidden business units, including from the pending count`() {
        Store.getInstance().setSnapshotForTest(
            listOf(
                state(infra, runs = listOf(run("r1", "w1", "awaiting_approval"))),
                state(apps, runs = listOf(run("r2", "w2", "awaiting_approval"))),
            ),
        )
        val panel = runsPanel()
        assertEquals(2, panel.pendingApprovals())

        Store.getInstance().hiddenProvider = { setOf("apps") }

        assertEquals(1, panel.pendingApprovals())
        assertEquals(listOf("r1"), panel.runNodes().map { it.run.id })
        assertTrue(panel.rebuild().first() is FilterHeaderNode)
    }

    fun `test runs panel with no business units shows a message`() {
        Store.getInstance().setSnapshotForTest(emptyList(), emptyList())
        val panel = runsPanel()

        val roots = panel.rebuild()
        assertEquals(1, roots.size)
        assertEquals("No business units", (roots.single() as MessageNode).renderedText())
        assertEquals(0, panel.pendingApprovals())
    }

    // ─── real (async) tree: default expansion ─────────────────────────────────────

    /** The previous test's tearDown leaves a fire-and-forget Store refresh (a clear() against the
     *  signed-out session) in flight; let it land so it cannot wipe the snapshot seeded next. */
    private fun settleStore() {
        offEdt { Store.getInstance().refreshAndWait() }
        PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
    }

    private fun settledTree(panel: TreePanel): javax.swing.JTree {
        PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
        val tree = panel.treeComponent as javax.swing.JTree
        PlatformTestUtil.waitWhileBusy(tree)
        PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
        PlatformTestUtil.waitWhileBusy(tree)
        return tree
    }

    fun `test a lone visible business unit is expanded in the real tree`() {
        val panel = workspacesPanel()
        settleStore()
        Store.getInstance().setSnapshotForTest(listOf(state(infra, listOf(ws("w1", tfWorkingDir = "a/b/c")))))

        val tree = settledTree(panel)

        assertEquals("expected only the business unit row at the top", 1, tree.getPathForRow(0).pathCount - 1)
        PlatformTestUtil.waitWithEventsDispatching("the only business unit must be expanded by default", { tree.isExpanded(0) }, 5)
        PlatformTestUtil.waitWhileBusy(tree)
        assertTrue("its cloud group row must be visible", tree.rowCount >= 2)
    }

    fun `test several business units start collapsed in the real tree`() {
        val panel = workspacesPanel()
        settleStore()
        Store.getInstance().setSnapshotForTest(listOf(state(infra, listOf(ws("w1"))), state(apps, listOf(ws("w2")))))

        val tree = settledTree(panel)

        assertEquals(2, tree.rowCount)
        assertFalse(tree.isExpanded(0))
        assertFalse(tree.isExpanded(1))
    }

    // ─── data keys ─────────────────────────────────────────────────────────────

    fun `test selected workspace and run nodes publish their business unit slug`() {
        val workspaceNode = WorkspaceNode(project, null, "infra", ws("w1"), "w1")
        val runNode = RunNode(project, null, "apps", run("r1", "w1", "applied"))

        assertEquals("infra", workspaceNode.bu)
        assertEquals("apps", runNode.bu)
        assertEquals("terraducktel.bu", TdtDataKeys.BU.name)
    }

    // ─── RunNode step cache ────────────────────────────────────────────────────

    fun `test run node fetches steps once, reuses the cache on a rebuild, and only refetches expanded non-terminal runs on a store tick when steps changed`() {
        StubServer().use { srv ->
            srv.json("GET", "/api/v1/auth/config", 200, """{"mode":"local"}""")
            val stepsVersion = java.util.concurrent.atomic.AtomicInteger(1)
            srv.on("GET", "/api/v1/runs/r1/steps") { _, ex ->
                val status = if (stepsVersion.get() == 1) "running" else "success"
                StubServer.respond(ex, 200, """[{"position":1,"name":"init","status":"$status"}]""")
            }
            setProfile(srv)
            // RunNode reads TdtSession directly, not through Store — neutering Store's own
            // client keeps its background poll from racing setSnapshotForTest below without
            // affecting TdtSession's real (stubbed) client at all.
            Store.getInstance().clientProvider = { null }
            offEdt { TdtSession.getInstance().reload() }
            offEdt { TdtSession.getInstance().signInWithApiKey("tdt_x") }
            // reload()/signInWithApiKey() each fire-and-forget a Store.refresh() (a no-op clear()
            // with clientProvider neutered above) — flush it before seeding our own snapshot so it
            // can't land afterwards and wipe it out from under us.
            offEdt { Store.getInstance().refreshAndWait() }

            val theWs = ws(id = "w1", name = "vpc")
            val theRun = run(id = "r1", wsId = "w1", status = "running", createdAt = "2024-01-01T00:00:00Z")
            Store.getInstance().setSnapshotForTest(listOf(theWs), listOf(theRun))

            val panel = RunsPanel(project, testRootDisposable)
            panel.signedInProvider = { true }

            // First expand: cache miss, one fetch.
            panel.runNodes().single().buildChildren()
            awaitCondition { srv.calls("GET", "/api/v1/runs/r1/steps").isNotEmpty() }
            awaitCondition {
                panel.runNodes().single().buildChildren().any { it is StepNode }
            }
            assertEquals(1, srv.calls("GET", "/api/v1/runs/r1/steps").size)

            // A rebuild (a fresh RunNode instance, same run id — exactly what happens on every
            // real `invalidateAsync()`) must hit the cache, never fetch again.
            val secondBuildChildren = panel.runNodes().single().buildChildren()
            assertTrue(secondBuildChildren.filterIsInstance<StepNode>().isNotEmpty())
            Thread.sleep(150) // let an errant duplicate fetch show up if the fix regressed
            assertEquals(1, srv.calls("GET", "/api/v1/runs/r1/steps").size)

            // Not expanded anywhere — a store tick must not touch the network at all.
            panel.refreshExpandedSteps()
            Thread.sleep(150)
            assertEquals(1, srv.calls("GET", "/api/v1/runs/r1/steps").size)

            // Now mark it expanded (bypassing real Swing expand events — see TreePanel's KDoc)
            // and change what the stub returns; a tick must refetch exactly once more and pick up
            // the change.
            panel.expandedRunIds += "r1"
            stepsVersion.set(2)
            panel.refreshExpandedSteps()
            awaitCondition { srv.calls("GET", "/api/v1/runs/r1/steps").size == 2 }
            awaitCondition {
                panel.runNodes().single().buildChildren()
                    .filterIsInstance<StepNode>().singleOrNull()?.step?.status == "success"
            }

            // A second, identical tick still fetches-to-compare (there's no way to know it's
            // unchanged without asking) but must leave the cached (and displayed) steps alone —
            // "invalidate only if the result differs" is about not redrawing, not about skipping
            // the request.
            panel.refreshExpandedSteps()
            awaitCondition { srv.calls("GET", "/api/v1/runs/r1/steps").size == 3 }
            assertEquals(
                "success",
                panel.runNodes().single().buildChildren()
                    .filterIsInstance<StepNode>().single().step.status,
            )
        }
    }

    fun `test an expanded run that completes gets exactly one final refresh, then no more`() {
        StubServer().use { srv ->
            srv.json("GET", "/api/v1/auth/config", 200, """{"mode":"local"}""")
            val stepsVersion = java.util.concurrent.atomic.AtomicInteger(1)
            srv.on("GET", "/api/v1/runs/r1/steps") { _, ex ->
                val status = if (stepsVersion.get() == 1) "running" else "success"
                StubServer.respond(ex, 200, """[{"position":1,"name":"init","status":"$status"}]""")
            }
            setProfile(srv)
            Store.getInstance().clientProvider = { null }
            offEdt { TdtSession.getInstance().reload() }
            offEdt { TdtSession.getInstance().signInWithApiKey("tdt_x") }
            offEdt { Store.getInstance().refreshAndWait() }

            val theWs = ws(id = "w1", name = "vpc")
            val runningRun = run(id = "r1", wsId = "w1", status = "running", createdAt = "2024-01-01T00:00:00Z")
            Store.getInstance().setSnapshotForTest(listOf(theWs), listOf(runningRun))

            val panel = RunsPanel(project, testRootDisposable)
            panel.signedInProvider = { true }

            // Expand while still running: one fetch.
            panel.runNodes().single().buildChildren()
            awaitCondition {
                panel.runNodes().single().buildChildren().any { it is StepNode }
            }
            assertEquals(1, srv.calls("GET", "/api/v1/runs/r1/steps").size)
            panel.expandedRunIds += "r1"

            // The run completes (a later Store snapshot carries the new status) and its steps
            // changed — a tick must fetch exactly once more, mark the cache entry final, and show
            // the new steps.
            stepsVersion.set(2)
            val appliedRun = run(id = "r1", wsId = "w1", status = "applied", createdAt = "2024-01-01T00:00:00Z")
            Store.getInstance().setSnapshotForTest(listOf(theWs), listOf(appliedRun))
            panel.refreshExpandedSteps()
            awaitCondition { srv.calls("GET", "/api/v1/runs/r1/steps").size == 2 }
            awaitCondition {
                panel.runNodes().single().buildChildren()
                    .filterIsInstance<StepNode>().singleOrNull()?.step?.status == "success"
            }

            // Now final — a further tick (even though still "expanded") must not touch the
            // network at all.
            panel.refreshExpandedSteps()
            Thread.sleep(150)
            assertEquals(2, srv.calls("GET", "/api/v1/runs/r1/steps").size)
        }
    }

    fun `test a session change clears the run step cache`() {
        StubServer().use { srv ->
            srv.json("GET", "/api/v1/auth/config", 200, """{"mode":"local"}""")
            srv.json("GET", "/api/v1/runs/r1/steps", 200, """[{"position":1,"name":"init","status":"success"}]""")
            setProfile(srv)
            Store.getInstance().clientProvider = { null }
            offEdt { TdtSession.getInstance().reload() }
            offEdt { TdtSession.getInstance().signInWithApiKey("tdt_x") }
            offEdt { Store.getInstance().refreshAndWait() } // flush the fire-and-forget refresh() before seeding

            val theWs = ws(id = "w1", name = "vpc")
            val theRun = run(id = "r1", wsId = "w1", status = "applied")
            Store.getInstance().setSnapshotForTest(listOf(theWs), listOf(theRun))

            val panel = RunsPanel(project, testRootDisposable)
            panel.signedInProvider = { true }
            panel.runNodes().single().buildChildren()
            awaitCondition { srv.calls("GET", "/api/v1/runs/r1/steps").isNotEmpty() }
            awaitCondition {
                panel.runNodes().single().buildChildren().any { it is StepNode }
            }

            offEdt { TdtSession.getInstance().signOut() } // also clears the Store snapshot — re-seed it below
            offEdt { TdtSession.getInstance().reload() }
            offEdt { Store.getInstance().refreshAndWait() } // flush reload()'s fire-and-forget refresh() first
            // sessionChanged() is published via invokeLater — drain the EDT queue so TreePanel's
            // listener (which calls RunNode.clearAll()) actually runs before we assert.
            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
            Store.getInstance().setSnapshotForTest(listOf(theWs), listOf(theRun))

            // Signing out clears the whole step cache, so the very next build is a cache miss
            // again (shows the loading placeholder — with no signed-in client it never resolves,
            // since signing out also means clientOrNull() is now null).
            val childrenAfterSignOut = panel.runNodes().single().buildChildren()
            assertTrue(childrenAfterSignOut.none { it is StepNode })
        }
    }

    // ─── revealWorkspace ────────────────────────────────────────────────────────

    fun `test revealWorkspace visitor never descends into run, step or message subtrees`() {
        val targetWs = ws(id = "w1", name = "vpc")
        val otherWs = ws(id = "w2", name = "other")
        val matchingNode = WorkspaceNode(project, null, "infra", targetWs, "vpc")
        val nonMatchingNode = WorkspaceNode(project, null, "infra", otherWs, "other")
        val runNode = RunNode(project, null, "infra", run(id = "r1", wsId = "w1", status = "running"))
        val stepNode = StepNode(project, runNode, RunStep(position = 1, name = "init", status = "running"))
        val messageNode = MessageNode(project, null, "loading…")

        val targetId = "ws:w1"
        assertEquals(TreeVisitor.Action.INTERRUPT, TreePanel.revealAction(matchingNode, targetId))
        assertEquals(TreeVisitor.Action.SKIP_CHILDREN, TreePanel.revealAction(nonMatchingNode, targetId))
        assertEquals(TreeVisitor.Action.SKIP_CHILDREN, TreePanel.revealAction(runNode, targetId))
        assertEquals(TreeVisitor.Action.SKIP_CHILDREN, TreePanel.revealAction(stepNode, targetId))
        assertEquals(TreeVisitor.Action.SKIP_CHILDREN, TreePanel.revealAction(messageNode, targetId))
        assertEquals(TreeVisitor.Action.SKIP_CHILDREN, TreePanel.revealAction(null, targetId))
    }

    fun `test revealWorkspace visitor keeps descending through cloud, region and folder nodes`() {
        val awsWs = ws(
            id = "w1", name = "vpc", awsAccountId = "123456789012", region = "eu-west-1",
            tfWorkingDir = "account-123456789012/eu-west-1/vpc",
        )
        Store.getInstance().setSnapshotForTest(listOf(awsWs), emptyList())
        val panel = WorkspacesPanel(project, testRootDisposable)
        panel.signedInProvider = { true }
        val buNode = panel.rebuild().filterIsInstance<BuNode>().single()
        val cloudGroup = buNode.buildChildren().filterIsInstance<CloudGroupNode>().single()
        val region = cloudGroup.buildChildren().filterIsInstance<RegionNode>().single()

        assertEquals(TreeVisitor.Action.CONTINUE, TreePanel.revealAction(buNode, "ws:w1"))
        assertEquals(TreeVisitor.Action.SKIP_CHILDREN, TreePanel.revealAction(FilterHeaderNode(project, null, 1, 2), "ws:w1"))
        assertEquals(TreeVisitor.Action.CONTINUE, TreePanel.revealAction(cloudGroup, "ws:w1"))
        assertEquals(TreeVisitor.Action.CONTINUE, TreePanel.revealAction(region, "ws:w1"))
    }
}
