package com.terraducktel.jetbrains.toolwindow

import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.api.Workspace
import com.terraducktel.jetbrains.state.Store
import com.terraducktel.jetbrains.toolwindow.nodes.CloudGroupNode
import com.terraducktel.jetbrains.toolwindow.nodes.MessageNode
import com.terraducktel.jetbrains.toolwindow.nodes.RegionNode
import com.terraducktel.jetbrains.toolwindow.nodes.RunNode
import com.terraducktel.jetbrains.toolwindow.nodes.TdtNode
import com.terraducktel.jetbrains.toolwindow.nodes.WorkspaceNode

/**
 * Platform tests for Task 9's Workspaces/Runs trees: [WorkspacesPanel]/[RunsPanel] built on top
 * of the shared [TreePanel] plumbing. Uses [Store.setSnapshotForTest] to seed a fixed snapshot and
 * the [TreePanel.signedInProvider] / [TreePanel.profileConfiguredProvider] test seams to exercise
 * the "not usable yet" branch deterministically, without a real sign-in round trip. [TreePanel.
 * rebuild] computes the root's children synchronously (no async tree machinery), which is all
 * these tests need — the real [com.intellij.ui.tree.StructureTreeModel]/[com.intellij.ui.tree.
 * AsyncTreeModel] wiring is exercised indirectly by [TdtToolWindowFactoryTest].
 */
class TreesTest : BasePlatformTestCase() {

    override fun tearDown() {
        try {
            Store.getInstance().setSnapshotForTest(emptyList(), emptyList())
            Store.getInstance().setLastErrorForTest(null)
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
        assertNotNull(cfGroup)

        val regions = awsGroup.buildChildren().filterIsInstance<RegionNode>()
        assertEquals(1, regions.size)
        assertEquals("eu-west-1", regions.single().region.region)

        val leaves = regions.single().buildChildren().filterIsInstance<WorkspaceNode>()
        assertEquals(1, leaves.size)
        assertEquals("vpc", leaves.single().leaf)
        assertEquals("w1", leaves.single().ws.id)
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
}
