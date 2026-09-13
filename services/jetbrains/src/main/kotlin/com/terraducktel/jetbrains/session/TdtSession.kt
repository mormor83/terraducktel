package com.terraducktel.jetbrains.session

import com.intellij.ide.BrowserUtil
import com.intellij.notification.NotificationType
import com.intellij.openapi.Disposable
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.components.Service
import com.intellij.openapi.components.service
import com.intellij.openapi.progress.ProgressIndicator
import com.terraducktel.jetbrains.TdtLog
import com.terraducktel.jetbrains.actions.ActionUtil
import com.terraducktel.jetbrains.api.TdtClient
import com.terraducktel.jetbrains.auth.PasswordSafeSecretStore
import com.terraducktel.jetbrains.auth.SecretStore
import com.terraducktel.jetbrains.auth.Sso
import com.terraducktel.jetbrains.auth.SsoCancelled
import com.terraducktel.jetbrains.auth.TokenManager
import com.terraducktel.jetbrains.settings.Profile
import com.terraducktel.jetbrains.settings.TdtSettings
import com.terraducktel.jetbrains.settings.TdtSettingsListener
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * Everything that depends on "which deployment / which BU / who am I". Rebuilt on every profile
 * change ([reload]). Application-level light service — a blocking port of `services/vscode/src/
 * session.ts`'s `Session` class; every public method here is blocking (secret-store I/O, and
 * sometimes network I/O) and must be called off the EDT (actions do this via
 * [com.terraducktel.jetbrains.actions.ActionUtil.runBackground]).
 */
@Service(Service.Level.APP)
class TdtSession(private val scope: CoroutineScope) : Disposable {

    var profile: Profile? = null; private set
    var tokens: TokenManager? = null; private set
    var client: TdtClient? = null; private set
    var bu: String = ""; private set

    /** Bumped by every [reload]. A cycle that finds itself superseded mid-call bails out rather
     *  than publishing its (now stale) profile/client over a newer one's. */
    @Volatile private var reloadGen = 0

    /** Removers for the current cycle's listeners (client sign-out, token change) — invoked at the
     *  top of the next [reload] (or [dispose]) so they never fire for a superseded cycle. */
    private var cycleRemovers: List<() -> Unit> = emptyList()

    /** Aborts the loopback listener of an SSO sign-in that is still waiting for the browser. */
    @Volatile private var cancelSso: (() -> Unit)? = null

    /** Test seam: tests inject an in-memory [SecretStore] here before calling [reload]. */
    internal var secretStoreFactory: () -> SecretStore = { PasswordSafeSecretStore() }

    /** Invoked whenever the client signals a session expired sign-out. Wired to the run/workspace
     *  store once it exists (Task 8) — left null here since that store doesn't exist yet. */
    var onSignedOutHook: (() -> Unit)? = null

    /** Invoked at the end of every successful [reload]. Wired to the store (Task 8). */
    var onReloadedHook: (() -> Unit)? = null

    private val connection = ApplicationManager.getApplication().messageBus.connect(this)

    init {
        connection.subscribe(
            TdtSettingsListener.TOPIC,
            object : TdtSettingsListener {
                override fun settingsChanged() {
                    ApplicationManager.getApplication().executeOnPooledThread { reload() }
                }
            },
        )
    }

    fun isSignedIn(): Boolean = tokens?.isSignedIn() == true

    /** Port of `session.ts`'s `canWrite()`. */
    fun canWrite(): Boolean {
        val tm = tokens ?: return false
        if (!tm.isSignedIn()) return false
        val claims = tm.claims() ?: return tm.kind() == "api_key"
        return claims.is_superadmin == true || claims.role == "operator" || claims.role == "admin"
    }

    fun uiUrl(): String? = profile?.let { TdtSettings.getInstance().uiUrlFor(it) }

    /** null unless signed in — the gate any poller/store must use before issuing requests. */
    fun clientOrNull(): TdtClient? = if (tokens?.isSignedIn() == true) client else null

    /** Throws a friendly error when there is no profile / no session, mirroring `session.ts`. */
    fun requireClient(): TdtClient {
        if (profile == null) {
            throw IllegalStateException("No Terraducktel profile configured. Add one under Settings → Tools → Terraducktel.")
        }
        val c = client
        if (c == null || tokens?.isSignedIn() != true) {
            throw IllegalStateException("Not signed in to Terraducktel. Run “Terraducktel: Sign in”.")
        }
        return c
    }

    /** Rebuilds [profile]/[tokens]/[client]/[bu] from [TdtSettings]. Blocking — call off the EDT. */
    fun reload() {
        val gen = ++reloadGen
        cycleRemovers.forEach { it() }
        cycleRemovers = emptyList()

        val settings = TdtSettings.getInstance()
        val next = settings.activeProfile()
        profile = next
        if (next == null) {
            tokens = null
            client = null
            bu = ""
            publish()
            return
        }
        bu = settings.state.buByProfile[next.name] ?: ""
        val tm = TokenManager(secretStoreFactory(), next.name)
        tm.restore()
        if (gen != reloadGen) return // a newer reload() took over while we read the secret store

        val newClient = TdtClient(
            baseUrl = next.url,
            bu = bu,
            tokens = tm,
            insecureTls = next.insecureTls,
            trace = { line -> if (TdtSettings.getInstance().state.trace) TdtLog.trace(line) },
        )
        tm.attach(newClient)
        tokens = tm
        client = newClient

        // Captured so a listener firing late (after a LATER reload() replaced tokens/client
        // wholesale) can tell that it belongs to a superseded cycle and ignore itself. Not
        // guarded against `setBu()`, which intentionally keeps the same TokenManager/auth session.
        val cycleTokens = tm
        val removeSignedOut = newClient.onSignedOut {
            if (tokens !== cycleTokens) return@onSignedOut
            onSignedOutHook?.invoke()
            ApplicationManager.getApplication().invokeLater {
                ActionUtil.notify(
                    null,
                    "Terraducktel: session expired — sign in again.",
                    NotificationType.WARNING,
                    "Sign in" to { SignInFlow.start(null) },
                )
            }
            publish()
        }
        val removeOnDidChange = tm.onDidChange { publish() }
        cycleRemovers = listOf(removeSignedOut, removeOnDidChange)

        publish()
        if (gen != reloadGen) return
        onReloadedHook?.invoke()
    }

    /** Writes the active profile and rebuilds the session around it. */
    fun setActiveProfile(name: String) {
        TdtSettings.getInstance().state.activeProfile = name
        reload()
    }

    /** Swaps in a `withBu()` clone of the SAME client/auth session — deliberately NOT a [reload],
     *  so the sign-out listener and token-change listener installed this cycle keep firing. */
    fun setBu(slug: String) {
        val p = profile ?: return
        val c = client ?: return
        TdtSettings.getInstance().state.buByProfile[p.name] = slug
        bu = slug
        val newClient = c.withBu(slug)
        client = newClient
        tokens?.attach(newClient)
        publish()
    }

    fun signInWithPassword(email: String, password: String) {
        val tm = tokens ?: throw IllegalStateException("Add a profile under Settings → Tools → Terraducktel first.")
        tm.signInWithPassword(email, password)
    }

    fun signInWithApiKey(key: String) {
        val tm = tokens ?: throw IllegalStateException("Add a profile under Settings → Tools → Terraducktel first.")
        tm.signInWithApiKey(key)
    }

    /** Runs the SSO loopback flow and signs in with the resulting token pair. A second sign-in
     *  first cancels any loopback listener a previous attempt left waiting. [SsoCancelled] is a
     *  choice, not a failure — callers (see [SignInFlow]) swallow it rather than reporting it. */
    fun signInWithSso(indicator: ProgressIndicator) {
        val tm = tokens ?: throw IllegalStateException("Add a profile under Settings → Tools → Terraducktel first.")
        val c = client ?: throw IllegalStateException("Add a profile under Settings → Tools → Terraducktel first.")
        cancelSso?.invoke()
        var mine: (() -> Unit)? = null
        val pair = try {
            Sso.runLoopbackLogin(
                buildUrl = c::ssoLoginUrl,
                openUrl = { BrowserUtil.browse(it); true },
                onCancel = { cancel ->
                    mine = cancel
                    cancelSso = cancel
                    scope.launch {
                        while (isActive && !indicator.isCanceled) delay(250)
                        if (indicator.isCanceled) cancel()
                    }
                },
            )
        } finally {
            if (cancelSso === mine) cancelSso = null
        }
        tm.signInWithTokenPair(pair, "sso")
    }

    fun signOut() {
        tokens?.signOut()
    }

    private fun publish() {
        ApplicationManager.getApplication().invokeLater {
            ApplicationManager.getApplication().messageBus.syncPublisher(TdtSessionListener.TOPIC).sessionChanged()
        }
    }

    override fun dispose() {
        cancelSso?.invoke()
        cycleRemovers.forEach { it() }
        cycleRemovers = emptyList()
    }

    companion object {
        fun getInstance(): TdtSession = service()
    }
}
