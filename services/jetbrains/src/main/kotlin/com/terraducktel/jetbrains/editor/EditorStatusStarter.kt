package com.terraducktel.jetbrains.editor

import com.intellij.openapi.project.Project
import com.intellij.openapi.startup.ProjectActivity

/**
 * Touches [EditorStatus] once per opened project so its `init` block's `TdtSettingsListener`/
 * `TdtSessionListener`/`Store` subscriptions are live even when NEITHER status-bar widget factory
 * was available at IDE startup (e.g. "Show status bar item" was off, or no profile existed yet).
 *
 * [EditorStatus] is a lazy light service — without this, it is only instantiated the first time
 * something calls [EditorStatus.getInstance], which in production is
 * [TdtStatusBarWidget]/[ProfileStatusBarWidget] reading [EditorStatus.view]. But the platform only
 * ever calls a `StatusBarWidgetFactory.createWidget` when
 * [com.intellij.openapi.wm.StatusBarWidgetFactory.isAvailable] was already true — so a widget that
 * starts out disabled never gets created, [EditorStatus] never gets instantiated, its settings
 * listener never subscribes, and a LATER settings change that would re-enable it has nothing
 * listening to react — see [EditorStatus.refreshWidgetAvailability]. Forcing instantiation here,
 * unconditionally, closes that gap the same way
 * [com.terraducktel.jetbrains.session.TdtSessionStarter] does for [com.terraducktel.jetbrains
 * .session.TdtSession].
 */
class EditorStatusStarter : ProjectActivity {
    override suspend fun execute(project: Project) {
        EditorStatus.getInstance(project)
    }
}
