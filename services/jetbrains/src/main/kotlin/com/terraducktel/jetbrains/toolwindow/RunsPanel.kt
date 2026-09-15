package com.terraducktel.jetbrains.toolwindow

import com.intellij.openapi.Disposable
import com.intellij.openapi.project.Project
import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.state.Store
import com.terraducktel.jetbrains.toolwindow.nodes.MessageNode
import com.terraducktel.jetbrains.toolwindow.nodes.RunNode
import com.terraducktel.jetbrains.toolwindow.nodes.TdtNode

/** The "Runs" tab: every run in the current BU, flat (no workspace grouping), most-actionable
 *  first. See [TreePanel] for the shared tree plumbing. */
class RunsPanel(project: Project, parentDisposable: Disposable) : TreePanel(project, parentDisposable) {

    override fun popupGroupId(): String = "Terraducktel.RunMenu"

    override fun computeRootChildren(root: TdtNode): List<TdtNode> {
        if (!signedInProvider()) return listOf(notReadyMessage(root))

        val head = headMessages(root)
        val content = sortedRuns().map { RunNode(project, root, it) }
        return if (content.isEmpty() && Store.getInstance().lastError == null) {
            head + MessageNode(project, root, "No runs yet")
        } else {
            head + content
        }
    }

    /** Count of runs awaiting approval — used for the "Runs · N" tool window tab title. */
    fun pendingApprovals(): Int = Store.getInstance().runs.count { it.status == "awaiting_approval" }

    private fun sortedRuns(): List<Run> =
        Store.getInstance().runs.sortedWith(compareBy<Run> { rank(it.status) }.thenByDescending { it.created_at ?: "" })

    private fun rank(status: String): Int = when (status) {
        "awaiting_approval" -> 0
        "pending", "running", "planning", "applying" -> 1
        "planned", "applied", "failed", "cancelled" -> 2
        else -> 3
    }
}
