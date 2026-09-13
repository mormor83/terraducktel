package com.terraducktel.jetbrains.session

import com.intellij.openapi.project.Project
import com.intellij.openapi.startup.ProjectActivity
import com.terraducktel.jetbrains.notifications.ApprovalService
import com.terraducktel.jetbrains.output.Approvals
import com.terraducktel.jetbrains.output.PlanDocument
import com.terraducktel.jetbrains.output.RunActions

/**
 * Touches [TdtSession] once per IDE run so its application-level light service is actually
 * instantiated (light services are lazy — nothing else necessarily calls `getInstance()` at
 * startup), which in turn runs its `init` block's initial background [TdtSession.reload]. Without
 * this, `profile` stays null until some action happens to call [TdtSession.getInstance] first —
 * e.g. "Sign In…" would wrongly report "no profile configured" even when one is.
 *
 * Also wires [RunActions.showPlanHook]/[RunActions.approveHook] (Task 11) — the awaiting-approval
 * balloon's "Show plan"/"Approve…" actions — here rather than in some `object`'s lazy initializer,
 * so they're guaranteed set once per IDE session before any balloon can appear, regardless of
 * which class happens to load first. [RunActions.onAwaitingHook] (Task 4 of plan 2) is wired the
 * same way, to [ApprovalService.markSeen] — the run-output tail's own "awaiting approval" toast
 * must reach the background approval poll's dedupe set before the poll's next tick, or it would
 * announce the very same run a second time.
 */
class TdtSessionStarter : ProjectActivity {
    override suspend fun execute(project: Project) {
        TdtSession.getInstance()
        RunActions.showPlanHook = { p, r -> PlanDocument.open(p, r.id, RunActions.wsName(r)) }
        RunActions.approveHook = { p, r -> Approvals.approve(p, r) }
        RunActions.onAwaitingHook = { r -> ApprovalService.getInstance().markSeen(r.id) }
    }
}
