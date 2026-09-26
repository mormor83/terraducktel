package com.terraducktel.jetbrains.toolwindow.nodes

import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.api.RunStep
import com.terraducktel.jetbrains.api.Workspace
import com.terraducktel.jetbrains.state.Cloud
import com.terraducktel.jetbrains.state.CloudGroup
import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.ZoneOffset

/** Row text shared with the VS Code tree (`services/vscode/src/views/nodes.ts`). */
class NodeTextTest {

    private val ws = Workspace(id = "w1", name = "vpc", repo_ref = "main")

    @Test fun `workspace description is last status, branch and drift`() {
        assertEquals("failed · main · drift", NodeText.workspaceDescription(ws.copy(drift_status = "drifted"), "failed"))
        assertEquals("no runs · main", NodeText.workspaceDescription(ws, null))
        assertEquals("planned", NodeText.workspaceDescription(ws.copy(repo_ref = ""), "planned"))
    }

    @Test fun `run description is status, branch, short id and local time`() {
        val run = Run(id = "abcdef123456", workspace_id = "w1", command = "plan", status = "planned", branch = "main", created_at = "2026-09-12T10:00:00Z")
        assertEquals("planned · main · abcdef12 · 2026-09-12 12:00", NodeText.runDescription(run, ZoneOffset.ofHours(2)))
        assertEquals("pending · abcdef12", NodeText.runDescription(run.copy(status = "pending", branch = null, created_at = null), ZoneOffset.UTC))
    }

    @Test fun `an unparseable timestamp is shown as-is`() {
        val run = Run(id = "r", workspace_id = "w1", command = "plan", status = "planned", created_at = "yesterday")
        assertEquals("planned · r · yesterday", NodeText.runDescription(run, ZoneOffset.UTC))
    }

    @Test fun `run label names the workspace only outside a workspace row`() {
        val run = Run(id = "r", workspace_id = "w1", command = "apply", status = "planned")
        assertEquals("vpc · apply", NodeText.runLabel(run, "vpc", underWorkspace = false))
        assertEquals("apply", NodeText.runLabel(run, "vpc", underWorkspace = true))
    }

    @Test fun `step description is its duration in seconds`() {
        assertEquals("12s", NodeText.stepDescription(RunStep(position = 0, name = "Plan", status = "success", duration_seconds = 12.0)))
        assertEquals("1.5s", NodeText.stepDescription(RunStep(position = 0, name = "Plan", status = "success", duration_seconds = 1.5)))
        assertEquals("", NodeText.stepDescription(RunStep(position = 0, name = "Plan", status = "pending")))
    }

    @Test fun `cloud description is the provider and workspace count`() {
        assertEquals("AWS · 2", NodeText.cloudDescription(CloudGroup(Cloud.AWS, "k", "123", emptyList(), 2)))
    }
}
