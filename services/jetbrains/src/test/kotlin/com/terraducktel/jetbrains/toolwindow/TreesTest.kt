package com.terraducktel.jetbrains.toolwindow

import com.intellij.openapi.application.ApplicationManager
import com.intellij.testFramework.PlatformTestUtil
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.intellij.ui.tree.TreeVisitor
import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.api.RunStep
import com.terraducktel.jetbrains.api.Workspace
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.settings.Profile
import com.terraducktel.jetbrains.settings.TdtSettings
import com.terraducktel.jetbrains.state.Store
import com.terraducktel.jetbrains.testutil.StubServer
import com.terraducktel.jetbrains.toolwindow.nodes.CloudGroupNode
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

    // ─── WorkspacesPanel ───────────────────────────────────────────────────────

    fun `test workspaces tree groups aws by account region and an unlinked workspace by its top folder`() {
        val awsWs = ws(
            id = "w1", name = "vpc", awsAccountId = "123456789012", region = "eu-west-1",
            tfWorkingDir = "account-123456789012/eu-west-1/vpc",
        )
        val cfWs = ws(
            id = "w2", name = "dns", awsAccountId = null, region = "",
            tfWorkingDir = "cloudflare/tenant-home/dns",
        )
        Store.getInstance().setSnapshotForTest(listOf(awsWs, cfWs), emptyList())

        val panel = WorkspacesPanel(project, testRootDisposable)
        panel.signedInProvider = { true }

        val roots = panel.rebuild()
        assertEquals(2, roots.size)

        val awsGroup = roots.filterIsInstance<CloudGroupNode>().single { it.id == "cloud:AWS:123456789012" }
        val cfGroup = roots.filterIsInstance<CloudGroupNode>().single { it.group.label == "cloudflare" }

        val awsRegions = awsGroup.buildChildren().filterIsInstance<RegionNode>()
        assertEquals(1, awsRegions.size)
        assertEquals("eu-west-1", awsRegions.single().region.region)

        val awsLeaves = awsRegions.single().buildChildren().filterIsInstance<WorkspaceNode>()
        assertEquals(1, awsLeaves.size)
        assertEquals("vpc", awsLeaves.single().leaf)
        assertEquals("w1", awsLeaves.single().ws.id)

        // Descend cloudflare's region → "cloudflare" folder → "tenant-home" folder → the "dns"
        // workspace leaf (Grouping doesn't strip a non-AWS/Azure/GCP top path segment, so it
        // reappears as a folder under its own cloud group — this asserts that real, existing
        // shape rather than a simplified one).
        val cfRegions = cfGroup.buildChildren().filterIsInstance<RegionNode>()
        assertEquals(1, cfRegions.size)
        val topFolders = cfRegions.single().buildChildren().filterIsInstance<FolderTreeNode>()
        val cloudflareFolder = topFolders.single { it.folder.name == "cloudflare" }
        val tenantHomeFolder = cloudflareFolder.buildChildren().filterIsInstance<FolderTreeNode>().single { it.folder.name == "tenant-home" }
        val dnsLeaf = tenantHomeFolder.buildChildren().filterIsInstance<WorkspaceNode>().single()
        assertEquals("dns", dnsLeaf.leaf)
        assertEquals("w2", dnsLeaf.ws.id)
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
        Store.getInstance().setSnapshotForTest(listOf(ws(id = "w1")), emptyList())
        Store.getInstance().setLastErrorForTest(RuntimeException("boom"))

        val panel = WorkspacesPanel(project, testRootDisposable)
        panel.signedInProvider = { true }

        val roots = panel.rebuild()
        assertTrue(roots.isNotEmpty())
        val first = roots.first() as MessageNode
        assertEquals("Last refresh failed: boom", first.renderedText())
        assertTrue(roots.drop(1).any { it is CloudGroupNode })
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

        val panel = RunsPanel(project, testRootDisposable)
        panel.signedInProvider = { true }

        val runNodes = panel.rebuild().filterIsInstance<RunNode>()
        assertEquals(
            listOf("r-awaiting", "r-awaiting-2", "r-running", "r-applied"),
            runNodes.map { it.run.id },
        )
        assertEquals(2, panel.pendingApprovals())
    }

    fun `test runs panel with no runs shows a message`() {
        Store.getInstance().setSnapshotForTest(emptyList(), emptyList())
        val panel = RunsPanel(project, testRootDisposable)
        panel.signedInProvider = { true }

        val roots = panel.rebuild()
        assertEquals(1, roots.size)
        assertEquals("No runs yet", (roots.single() as MessageNode).renderedText())
        assertEquals(0, panel.pendingApprovals())
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
            panel.rebuild().filterIsInstance<RunNode>().single().buildChildren()
            awaitCondition { srv.calls("GET", "/api/v1/runs/r1/steps").isNotEmpty() }
            awaitCondition {
                panel.rebuild().filterIsInstance<RunNode>().single().buildChildren().any { it is StepNode }
            }
            assertEquals(1, srv.calls("GET", "/api/v1/runs/r1/steps").size)

            // A rebuild (a fresh RunNode instance, same run id — exactly what happens on every
            // real `invalidateAsync()`) must hit the cache, never fetch again.
            val secondBuildChildren = panel.rebuild().filterIsInstance<RunNode>().single().buildChildren()
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
                panel.rebuild().filterIsInstance<RunNode>().single().buildChildren()
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
                panel.rebuild().filterIsInstance<RunNode>().single().buildChildren()
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
            panel.rebuild().filterIsInstance<RunNode>().single().buildChildren()
            awaitCondition {
                panel.rebuild().filterIsInstance<RunNode>().single().buildChildren().any { it is StepNode }
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
                panel.rebuild().filterIsInstance<RunNode>().single().buildChildren()
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
            panel.rebuild().filterIsInstance<RunNode>().single().buildChildren()
            awaitCondition { srv.calls("GET", "/api/v1/runs/r1/steps").isNotEmpty() }
            awaitCondition {
                panel.rebuild().filterIsInstance<RunNode>().single().buildChildren().any { it is StepNode }
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
            val childrenAfterSignOut = panel.rebuild().filterIsInstance<RunNode>().single().buildChildren()
            assertTrue(childrenAfterSignOut.none { it is StepNode })
        }
    }

    // ─── revealWorkspace ────────────────────────────────────────────────────────

    fun `test revealWorkspace visitor never descends into run, step or message subtrees`() {
        val targetWs = ws(id = "w1", name = "vpc")
        val otherWs = ws(id = "w2", name = "other")
        val matchingNode = WorkspaceNode(project, null, targetWs, "vpc")
        val nonMatchingNode = WorkspaceNode(project, null, otherWs, "other")
        val runNode = RunNode(project, null, run(id = "r1", wsId = "w1", status = "running"))
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
        val cloudGroup = panel.rebuild().filterIsInstance<CloudGroupNode>().single()
        val region = cloudGroup.buildChildren().filterIsInstance<RegionNode>().single()

        assertEquals(TreeVisitor.Action.CONTINUE, TreePanel.revealAction(cloudGroup, "ws:w1"))
        assertEquals(TreeVisitor.Action.CONTINUE, TreePanel.revealAction(region, "ws:w1"))
    }
}
