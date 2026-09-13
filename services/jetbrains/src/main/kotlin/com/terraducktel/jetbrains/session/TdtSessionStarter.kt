package com.terraducktel.jetbrains.session

import com.intellij.openapi.project.Project
import com.intellij.openapi.startup.ProjectActivity

/**
 * Touches [TdtSession] once per IDE run so its application-level light service is actually
 * instantiated (light services are lazy — nothing else necessarily calls `getInstance()` at
 * startup), which in turn runs its `init` block's initial background [TdtSession.reload]. Without
 * this, `profile` stays null until some action happens to call [TdtSession.getInstance] first —
 * e.g. "Sign In…" would wrongly report "no profile configured" even when one is.
 */
class TdtSessionStarter : ProjectActivity {
    override suspend fun execute(project: Project) {
        TdtSession.getInstance()
    }
}
