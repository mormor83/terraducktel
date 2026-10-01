package com.terraducktel.jetbrains.editor

import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.terraducktel.jetbrains.api.BusinessUnit
import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.api.Workspace

/**
 * [EditorStatus.actionsPopup]'s pure decision helpers — [EditorStatus.popupItems] (labels only) and
 * [EditorStatus.popupTitle] — extracted so this doesn't need a real Swing popup or a git checkout
 * (that's [EditorStatusTest]'s job, which also gates its whole class on `git` being on `PATH`; these
 * don't touch git at all, so they stay in their own class and always run).
 */
class EditorStatusPopupTest : BasePlatformTestCase() {

    private val infra = BusinessUnit("1", "infra", "Infra")
    private val apps = BusinessUnit("2", "apps", "Apps")

    private fun ws(name: String = "prod") = Workspace(id = "w1", name = name, tf_working_dir = "envs/prod")

    private fun runOf(id: String = "r1") =
        Run(id = id, workspace_id = "w1", command = "apply", status = "applied")

    fun testUnmappedCollapsesToOpenInBrowserOnlyRegardlessOfLastRun() {
        val status = EditorStatus.getInstance(project)
        assertEquals(listOf("Open in browser"), status.popupItems(null, null))
        assertEquals(listOf("Open in browser"), status.popupItems(null, runOf()))
    }

    fun testShowLastPlanAppearsOnlyWhenALastRunExists() {
        val status = EditorStatus.getInstance(project)
        val cur = CurrentFile(ws(), infra, git = null, exact = true, resolvedPath = "/repo/envs/prod/main.tf")

        assertEquals(
            listOf("Plan this leaf", "Reveal in tool window", "Open in browser"),
            status.popupItems(cur, null),
        )
        assertEquals(
            listOf("Plan this leaf", "Show last plan", "Reveal in tool window", "Open in browser"),
            status.popupItems(cur, runOf()),
        )
    }

    fun testTitleIsNullWhenUnmappedAndCarriesTheBranchSuffixWhenABranchIsKnown() {
        val status = EditorStatus.getInstance(project)
        assertNull(status.popupTitle(null))

        val curNoBranch = CurrentFile(ws(), infra, git = null, exact = true, resolvedPath = "/repo/envs/prod/main.tf")
        assertEquals("prod · envs/prod", status.popupTitle(curNoBranch))

        val curWithBranch = CurrentFile(ws(), infra, git = GitInfo("/repo", branch = "feature/x"), exact = true, resolvedPath = "/repo/envs/prod/main.tf")
        assertEquals("prod · envs/prod · branch feature/x", status.popupTitle(curWithBranch))
    }

    fun testAmbiguousFileOffersTheActionsThatCanAskWhichWorkspaceButNoShowLastPlan() {
        val status = EditorStatus.getInstance(project)
        assertEquals(
            listOf("Plan this leaf", "Reveal in tool window", "Open in browser"),
            status.popupItems(null, null, ambiguous = true),
        )
    }

    fun testChooserLabelsNameTheWorkspaceAndItsBusinessUnit() {
        val a = CurrentFile(ws("prod"), infra, git = null, exact = true, resolvedPath = "/repo/envs/prod/main.tf")
        val b = CurrentFile(ws("prod"), apps, git = null, exact = true, resolvedPath = "/repo/envs/prod/main.tf")
        assertEquals("prod — Infra", EditorStatus.chooserLabel(a))
        assertEquals("prod — Apps", EditorStatus.chooserLabel(b))
    }

    fun testTitleOfAnAmbiguousFileCountsTheMatches() {
        val status = EditorStatus.getInstance(project)
        assertEquals("2 workspaces match this file", status.popupTitle(null, ambiguousCount = 2))
    }
}
