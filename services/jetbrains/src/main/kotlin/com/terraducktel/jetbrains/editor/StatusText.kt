package com.terraducktel.jetbrains.editor

import com.terraducktel.jetbrains.api.Run

/**
 * Pure presentation for the current-file status-bar item — a port of `status.ts`'s private
 * `set()` method (text + tooltip only; `status.ts`'s `StatusBarItem.backgroundColor` becomes
 * [Severity] here, interpreted by the widget/platform however it likes). No platform imports, so
 * every text/tooltip/severity combination is covered by plain-JUnit [StatusTextTest].
 */
object StatusText {

    /** Mirrors the three `backgroundColor` states `status.ts` sets on the last run's status. */
    enum class Severity { NONE, WARNING, ERROR }

    data class View(val text: String, val tooltip: String, val severity: Severity)

    /** [cur] is mapped to a workspace; [lastRun] is that workspace's most recent run, if any. */
    fun mapped(cur: CurrentFile, lastRun: Run?): View {
        val ws = cur.ws
        val branch = cur.git?.branch
        val branchNote = if (branch != null && branch != ws.repo_ref) " · on $branch (tracks ${ws.repo_ref})" else ""
        val statusNote = if (lastRun != null) " · ${lastRun.status}" else ""
        val text = "TDT: ${ws.name}$statusNote"
        val parentNote = if (cur.exact) "" else " (parent leaf)"
        val tooltip = "${ws.tf_working_dir}$parentNote$branchNote\nClick for actions"
        val severity = when (lastRun?.status) {
            "failed" -> Severity.ERROR
            "awaiting_approval" -> Severity.WARNING
            else -> Severity.NONE
        }
        return View(text, tooltip, severity)
    }

    /** No workspace covers the active file. [git] is non-null when the file is at least inside a
     *  git checkout Terraducktel could inspect (just none of the configured workspaces claim it). */
    fun unmapped(git: GitInfo?): View {
        val tooltip = if (git != null) {
            "No Terraducktel workspace covers this path. Click to open Discover in the browser."
        } else {
            "Not inside a git checkout Terraducktel knows about."
        }
        return View("TDT: not imported", tooltip, Severity.NONE)
    }
}
