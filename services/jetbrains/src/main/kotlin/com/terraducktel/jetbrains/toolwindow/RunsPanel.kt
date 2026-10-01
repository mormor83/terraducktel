package com.terraducktel.jetbrains.toolwindow

import com.intellij.openapi.Disposable
import com.intellij.openapi.project.Project
import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.state.Store
import com.terraducktel.jetbrains.toolwindow.nodes.BuNode
import com.terraducktel.jetbrains.toolwindow.nodes.TdtNode

/** The "Runs" section: the same visible business units as the Workspaces section, each holding
 *  that BU's runs flat (no workspace grouping), most-actionable first. See [TreePanel] for the
 *  shared tree plumbing. */
class RunsPanel(project: Project, parentDisposable: Disposable) : TreePanel(project, parentDisposable) {

    override fun popupGroupId(): String = "Terraducktel.RunMenu"

    override fun computeRootChildren(root: TdtNode): List<TdtNode> = buRootChildren(root, BuNode.View.RUNS)

    /** Count of runs awaiting approval across the visible BUs — shown as the Runs section header's
     *  count pill. */
    fun pendingApprovals(): Int = Store.getInstance().allRuns().count { it.run.status == "awaiting_approval" }

    companion object {
        /** A BU's runs in display order: awaiting approval first, then in flight, then settled,
         *  newest first within each. */
        internal fun sortRuns(runs: List<Run>): List<Run> =
            runs.sortedWith(compareBy<Run> { rank(it.status) }.thenByDescending { it.created_at ?: "" })

        private fun rank(status: String): Int = when (status) {
            "awaiting_approval" -> 0
            "pending", "running", "planning", "applying" -> 1
            "planned", "applied", "failed", "cancelled" -> 2
            else -> 3
        }
    }
}
