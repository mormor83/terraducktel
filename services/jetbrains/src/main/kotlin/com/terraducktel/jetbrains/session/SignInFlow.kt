package com.terraducktel.jetbrains.session

import com.intellij.idea.AppMode
import com.intellij.notification.NotificationType
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.options.ShowSettingsUtil
import com.intellij.openapi.progress.ProgressIndicator
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.Messages
import com.intellij.openapi.ui.popup.JBPopupFactory
import com.intellij.ui.SimpleListCellRenderer
import com.terraducktel.jetbrains.actions.ActionUtil
import com.terraducktel.jetbrains.api.ApiError
import com.terraducktel.jetbrains.api.AuthConfig
import com.terraducktel.jetbrains.auth.SsoCancelled
import com.terraducktel.jetbrains.settings.Profile
import com.terraducktel.jetbrains.settings.TdtConfigurable
import java.io.IOException

/** Drives the interactive sign-in UI: mode selection (SSO / password / API key) followed by the
 *  matching dialog(s) and a background sign-in call against [TdtSession]. Port of `session.ts`'s
 *  `signIn()`, split out of the session service since it is pure UI orchestration. */
object SignInFlow {

    /** The three ways to sign in, each carrying its own popup label — dispatch keys off this enum,
     *  not off the label text, so relabelling a mode in the popup can never silently disable it. */
    private enum class Mode(val label: String) {
        SSO("Sign in with SSO"),
        PASSWORD("Email + password"),
        API_KEY("API key (tdt_…)"),
    }

    fun start(project: Project?) {
        val session = TdtSession.getInstance()
        val profile = session.profile
        if (profile == null) {
            ActionUtil.notify(
                project,
                "Add a profile under Settings → Tools → Terraducktel first.",
                NotificationType.ERROR,
                "Open settings" to { ShowSettingsUtil.getInstance().showSettingsDialog(project, TdtConfigurable::class.java) },
            )
            return
        }
        ActionUtil.runBackground(project, "Terraducktel: checking sign-in options…") {
            val cfg = try {
                session.client?.authConfig() ?: AuthConfig()
            } catch (_: ApiError) {
                AuthConfig()
            } catch (_: IOException) {
                AuthConfig()
            }
            ApplicationManager.getApplication().invokeLater { pickMode(project, session, profile, cfg) }
        }
    }

    private fun pickMode(project: Project?, session: TdtSession, profile: Profile, cfg: AuthConfig) {
        val modes = mutableListOf<Mode>()
        if (cfg.oidc_enabled && cfg.cli_loopback == true && !AppMode.isRemoteDevHost()) modes += Mode.SSO
        if (cfg.mode != "oidc") modes += Mode.PASSWORD
        modes += Mode.API_KEY

        if (modes.size == 1) {
            proceed(project, session, profile, modes.single())
            return
        }
        val popup = JBPopupFactory.getInstance()
            .createPopupChooserBuilder(modes)
            .setRenderer(SimpleListCellRenderer.create("") { it.label })
            .setTitle("Sign in to ${profile.name}")
            .setItemChosenCallback { proceed(project, session, profile, it) }
            .createPopup()
        if (project != null) popup.showCenteredInCurrentWindow(project) else popup.showInFocusCenter()
    }

    private fun proceed(project: Project?, session: TdtSession, profile: Profile, mode: Mode) {
        when (mode) {
            Mode.PASSWORD -> {
                val email = Messages.showInputDialog(project, "Email", "Sign in to ${profile.name}", null)
                if (email.isNullOrBlank()) return
                val password = Messages.showPasswordDialog(project, "Password", "Sign in to ${profile.name}", null) ?: return
                ActionUtil.runBackground(project, "Terraducktel: signing in…") {
                    session.signInWithPassword(email, password)
                    notifySuccess(project, session, profile)
                }
            }
            Mode.API_KEY -> {
                val key = Messages.showPasswordDialog(project, "API key (tdt_…)", "Sign in to ${profile.name}", null) ?: return
                ActionUtil.runBackground(project, "Terraducktel: signing in…") {
                    session.signInWithApiKey(key)
                    notifySuccess(project, session, profile)
                }
            }
            Mode.SSO -> {
                ActionUtil.runBackground(project, "Terraducktel: complete sign-in in your browser…", cancellable = true) { indicator: ProgressIndicator ->
                    try {
                        session.signInWithSso(indicator)
                        notifySuccess(project, session, profile)
                    } catch (_: SsoCancelled) {
                        // Cancelling is a choice, not a failure — leave quietly.
                    }
                }
            }
        }
    }

    private fun notifySuccess(project: Project?, session: TdtSession, profile: Profile) {
        val tokens = session.tokens
        val who = tokens?.claims()?.email ?: if (tokens?.kind() == "api_key") "API key" else "user"
        ActionUtil.notify(project, "Terraducktel: signed in to ${profile.name} as $who.")
    }
}
