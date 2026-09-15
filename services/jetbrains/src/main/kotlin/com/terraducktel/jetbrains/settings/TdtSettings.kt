package com.terraducktel.jetbrains.settings

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.components.PersistentStateComponent
import com.intellij.openapi.components.Service
import com.intellij.openapi.components.State
import com.intellij.openapi.components.Storage
import com.intellij.openapi.components.service
import com.intellij.util.messages.Topic
import com.intellij.util.xmlb.XmlSerializerUtil

/**
 * Fired on the application message bus whenever a [TdtSettings] change is committed that the rest
 * of the plugin needs to react to — active profile switched/renamed, its URL or TLS setting
 * changed, or a profile removed (see [TdtConfigurable.apply]). Later tasks (client/token-manager
 * lifecycle, status bar, run poller) subscribe to this instead of polling the service.
 */
interface TdtSettingsListener {
    fun settingsChanged()

    companion object {
        val TOPIC: Topic<TdtSettingsListener> = Topic.create("Terraducktel settings", TdtSettingsListener::class.java)
    }
}

/** Persistent, application-level settings: configured profiles + general plugin preferences.
 *  Never holds a secret — credentials live in PasswordSafe, keyed `terraducktel.cred.<profile>`
 *  (see [com.terraducktel.jetbrains.auth.PasswordSafeSecretStore]). */
@Service(Service.Level.APP)
@State(name = "TerraducktelSettings", storages = [Storage("terraducktel.xml")])
class TdtSettings : PersistentStateComponent<TdtSettings.State> {

    class State {
        var profiles: MutableList<Profile> = mutableListOf()
        var activeProfile: String = ""
        var buByProfile: MutableMap<String, String> = mutableMapOf()
        var refreshIntervalSeconds: Int = 30
        var runsLimit: Int = 200
        var approvalsPollSeconds: Int = 60
        var trace: Boolean = false
        var statusBarEnabled: Boolean = true
        var notifiedRuns: MutableMap<String, Long> = mutableMapOf()
    }

    private var myState = State()

    override fun getState(): State = myState
    override fun loadState(state: State) = XmlSerializerUtil.copyBean(state, myState)

    /** The profile with this exact name, or null. */
    fun profile(name: String): Profile? = myState.profiles.find { it.name == name }

    /** [State.activeProfile] resolved to a profile: the named one if it still exists, else the
     *  first profile by name (alphabetical, matching the VS Code extension's `pickActive`
     *  fallback), else null when there are no profiles at all. */
    fun activeProfile(): Profile? = profile(myState.activeProfile) ?: myState.profiles.minByOrNull { it.name }

    /** [Profile.uiUrl] when set, else [Profile.url] — either way with a trailing slash stripped. */
    fun uiUrlFor(p: Profile): String = p.uiUrl.ifBlank { p.url }.trimEnd('/')

    /** Publishes [TdtSettingsListener.TOPIC]. Call after committing a change other components
     *  must react to (see [TdtConfigurable.apply] for exactly which changes qualify). */
    fun fireChanged() {
        ApplicationManager.getApplication().messageBus.syncPublisher(TdtSettingsListener.TOPIC).settingsChanged()
    }

    companion object {
        fun getInstance(): TdtSettings = service()
    }
}
