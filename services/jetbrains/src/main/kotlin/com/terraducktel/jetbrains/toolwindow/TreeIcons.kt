package com.terraducktel.jetbrains.toolwindow

import com.intellij.icons.AllIcons
import javax.swing.Icon

/** Status → icon mappings shared by [com.terraducktel.jetbrains.toolwindow.nodes.WorkspaceNode],
 *  [com.terraducktel.jetbrains.toolwindow.nodes.RunNode] and [com.terraducktel.jetbrains.
 *  toolwindow.nodes.StepNode] — a port of `services/vscode/src/views/nodes.ts`'s `statusIcon`,
 *  restricted to `AllIcons.*` (no custom colours; see this plugin's "no new colours" rule). */
object TreeIcons {

    /** A workspace's last-run icon, or a run row's own icon. `null`/unknown → [AllIcons.Nodes.
     *  Module] (also used for a workspace with no runs at all). */
    fun runStatusIcon(status: String?): Icon = when (status) {
        "applied" -> AllIcons.RunConfigurations.TestPassed
        "failed" -> AllIcons.RunConfigurations.TestFailed
        "awaiting_approval" -> AllIcons.RunConfigurations.TestPaused
        "cancelled" -> AllIcons.RunConfigurations.TestIgnored
        "running", "planning", "applying", "pending" -> AllIcons.Process.Step_1
        else -> AllIcons.Nodes.Module
    }

    fun stepStatusIcon(status: String?): Icon = when (status) {
        "success" -> AllIcons.RunConfigurations.TestPassed
        "failed" -> AllIcons.RunConfigurations.TestFailed
        "running" -> AllIcons.Process.Step_1
        else -> AllIcons.Nodes.EmptyNode
    }
}
