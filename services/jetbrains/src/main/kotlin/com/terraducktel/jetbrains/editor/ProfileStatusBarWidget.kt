package com.terraducktel.jetbrains.editor

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.popup.JBPopup
import com.intellij.openapi.wm.StatusBar
import com.intellij.openapi.wm.StatusBarWidget
import com.intellij.openapi.wm.StatusBarWidgetFactory
import com.terraducktel.jetbrains.actions.auth.SwitchProfileAction
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.session.TdtSessionListener
import com.terraducktel.jetbrains.settings.TdtSettings
import com.terraducktel.jetbrains.settings.TdtSettingsListener

/** Registers [ProfileStatusBarWidget] (id `Terraducktel.Profile`) as a `statusBarWidgetFactory`,
 *  ordered before [TdtStatusBarWidgetFactory] — the profile widget sits to its left. */
class ProfileStatusBarWidgetFactory : StatusBarWidgetFactory {
    override fun getId(): String = ID
    override fun getDisplayName(): String = "Terraducktel: profile"
    override fun isAvailable(project: Project): Boolean = TdtSettings.getInstance().state.profiles.isNotEmpty()
    override fun createWidget(project: Project): StatusBarWidget = ProfileStatusBarWidget(project)

    companion object {
        const val ID = "Terraducktel.Profile"
    }
}

/**
 * Compact status-bar item showing the active Terraducktel profile (and BU, once signed in) — a
 * port of `services/vscode/src/views/profileStatus.ts`'s `ProfileStatus`. Hidden entirely (via
 * [ProfileStatusBarWidgetFactory.isAvailable] and a null [getSelectedValue]) when no profile is
 * configured. Click reuses [SwitchProfileAction]'s own chooser popup.
 */
class ProfileStatusBarWidget(private val project: Project) : StatusBarWidget, StatusBarWidget.MultipleTextValuesPresentation {
    override fun ID(): String = ProfileStatusBarWidgetFactory.ID
    override fun getPresentation(): StatusBarWidget.WidgetPresentation = this
    override fun getTooltipText(): String = "Terraducktel profile — click to switch"
    override fun getPopup(): JBPopup = SwitchProfileAction.buildPopup(project)

    override fun getSelectedValue(): String? {
        if (TdtSettings.getInstance().state.profiles.isEmpty()) return null
        val session = TdtSession.getInstance()
        val name = session.profile?.name ?: "no profile"
        val bu = session.bu.takeIf { session.isSignedIn() && it.isNotBlank() }
        return if (bu != null) "$name · $bu" else name
    }

    /** No own polling: the platform re-renders on [StatusBar.updateWidget], so this widget just
     *  needs to be told when the profile, BU, sign-in state, or profile list changes. */
    override fun install(statusBar: StatusBar) {
        val connection = ApplicationManager.getApplication().messageBus.connect(this)
        connection.subscribe(
            TdtSessionListener.TOPIC,
            object : TdtSessionListener {
                override fun sessionChanged() = statusBar.updateWidget(ID())
            },
        )
        connection.subscribe(
            TdtSettingsListener.TOPIC,
            object : TdtSettingsListener {
                override fun settingsChanged() = statusBar.updateWidget(ID())
            },
        )
    }

    override fun dispose() {}
}
