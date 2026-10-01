package com.terraducktel.jetbrains.editor

import com.intellij.icons.AllIcons
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.popup.JBPopup
import com.intellij.openapi.wm.StatusBarWidget
import com.intellij.openapi.wm.StatusBarWidgetFactory
import com.terraducktel.jetbrains.settings.TdtSettings
import javax.swing.Icon

/** Registers [TdtStatusBarWidget] (id `Terraducktel.CurrentFile`) as a `statusBarWidgetFactory`. */
class TdtStatusBarWidgetFactory : StatusBarWidgetFactory {
    override fun getId(): String = ID
    override fun getDisplayName(): String = "Terraducktel: current file"
    override fun isAvailable(project: Project): Boolean = TdtSettings.getInstance().state.statusBarEnabled
    override fun createWidget(project: Project): StatusBarWidget = TdtStatusBarWidget(project)

    companion object {
        const val ID = "Terraducktel.CurrentFile"
    }
}

/**
 * Status-bar item showing which Terraducktel workspace the active Terraform file belongs to — a
 * port of `services/vscode/src/editor/status.ts`'s `StatusBarItem`, backed by [EditorStatus].
 * [EditorStatus] itself calls `StatusBar.updateWidget` after every [EditorStatus.refresh]; this
 * widget only ever reads [EditorStatus.view] at render time.
 *
 * Returning `null` from [getSelectedValue] is how the platform's other status-bar widgets (e.g.
 * the built-in encoding/line-separator ones) hide themselves without a separate add/remove dance —
 * [EditorStatus.view] is `null` exactly when the item should be hidden (see its class doc).
 */
class TdtStatusBarWidget(private val project: Project) : StatusBarWidget, StatusBarWidget.MultipleTextValuesPresentation {
    override fun ID(): String = TdtStatusBarWidgetFactory.ID
    override fun getPresentation(): StatusBarWidget.WidgetPresentation = this
    override fun getSelectedValue(): String? = EditorStatus.getInstance(project).view?.text
    override fun getTooltipText(): String? = EditorStatus.getInstance(project).view?.tooltip
    override fun getPopup(): JBPopup = EditorStatus.getInstance(project).actionsPopup()

    /** Platform-provided icons only (never a hard-coded color) standing in for `status.ts`'s
     *  `StatusBarItem.backgroundColor` — the same [StatusText.Severity] the ERROR/WARNING
     *  background there was keyed on. */
    override fun getIcon(): Icon? = when (EditorStatus.getInstance(project).view?.severity) {
        StatusText.Severity.ERROR -> AllIcons.General.Error
        StatusText.Severity.WARNING -> AllIcons.General.Warning
        else -> null
    }

    override fun dispose() {}
}
