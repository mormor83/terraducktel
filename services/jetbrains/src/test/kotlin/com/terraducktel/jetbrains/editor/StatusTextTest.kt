package com.terraducktel.jetbrains.editor

import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.api.Workspace
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Plain-JUnit port of `status.ts`'s private `set()` method (text/tooltip/severity only — the
 * `StatusBarItem` plumbing itself is covered by [EditorStatusTest]). [StatusText] has no platform
 * imports, so every combination is exercised here without any IDE fixture.
 */
class StatusTextTest {

    private val ws = Workspace(id = "w1", name = "vpc", tf_working_dir = "envs/prod", repo_ref = "main")

    private fun runOf(status: String) = Run(id = "r1", workspace_id = "w1", command = "plan", status = status)

    private fun curFor(git: GitInfo? = null, exact: Boolean = true, path: String = "/repo/envs/prod/main.tf") =
        CurrentFile(ws, git, exact, path)

    @Test fun `a failed last run is ERROR severity with the status suffixed onto the text`() {
        val view = StatusText.mapped(curFor(), runOf("failed"))
        assertEquals("TDT: vpc · failed", view.text)
        assertEquals(StatusText.Severity.ERROR, view.severity)
    }

    @Test fun `a run awaiting approval is WARNING severity`() {
        val view = StatusText.mapped(curFor(), runOf("awaiting_approval"))
        assertEquals("TDT: vpc · awaiting_approval", view.text)
        assertEquals(StatusText.Severity.WARNING, view.severity)
    }

    @Test fun `no last run has no status suffix and NONE severity`() {
        val view = StatusText.mapped(curFor(), null)
        assertEquals("TDT: vpc", view.text)
        assertEquals(StatusText.Severity.NONE, view.severity)
    }

    @Test fun `a run in an ordinary state is NONE severity`() {
        val view = StatusText.mapped(curFor(), runOf("planned"))
        assertEquals("TDT: vpc · planned", view.text)
        assertEquals(StatusText.Severity.NONE, view.severity)
    }

    @Test fun `a parent-leaf match and branch drift both appear in the tooltip`() {
        val git = GitInfo(root = "/repo", remoteUrl = "https://github.com/acme/infra", branch = "feature/x")
        val view = StatusText.mapped(curFor(git = git, exact = false, path = "/repo/envs/prod/nested/main.tf"), null)
        assertTrue(view.tooltip.contains("(parent leaf)"))
        assertTrue(view.tooltip.contains("on feature/x (tracks main)"))
        assertTrue(view.tooltip.endsWith("Click for actions"))
    }

    @Test fun `an exact match on the tracked branch carries no parenthetical and no branch note`() {
        val git = GitInfo(root = "/repo", remoteUrl = "https://github.com/acme/infra", branch = "main")
        val view = StatusText.mapped(curFor(git = git, exact = true), null)
        assertEquals("envs/prod\nClick for actions", view.tooltip)
    }

    @Test fun `unmapped with a known git remote points at Discover`() {
        val git = GitInfo(root = "/repo", remoteUrl = "https://github.com/acme/other", branch = "main")
        val view = StatusText.unmapped(git)
        assertEquals("TDT: not imported", view.text)
        assertEquals(StatusText.Severity.NONE, view.severity)
        assertTrue(view.tooltip.startsWith("No Terraducktel workspace covers this path"))
    }

    @Test fun `unmapped outside a git checkout has the no-checkout tooltip`() {
        val view = StatusText.unmapped(null)
        assertEquals("TDT: not imported", view.text)
        assertTrue(view.tooltip.startsWith("Not inside a git checkout"))
    }
}
