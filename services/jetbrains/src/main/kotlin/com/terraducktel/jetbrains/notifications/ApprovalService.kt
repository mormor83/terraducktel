package com.terraducktel.jetbrains.notifications

import com.intellij.ide.impl.ProjectUtil
import com.intellij.openapi.Disposable
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.components.Service
import com.intellij.openapi.components.service
import com.intellij.openapi.project.Project
import com.intellij.openapi.project.ProjectManager
import com.intellij.openapi.startup.ProjectActivity
import com.terraducktel.jetbrains.TdtLog
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.session.TdtSessionListener
import com.terraducktel.jetbrains.settings.TdtSettings
import com.terraducktel.jetbrains.settings.TdtSettingsListener
import com.terraducktel.jetbrains.state.Store
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * Application service owning the one [ApprovalWatcher] the IDE runs. Wires it to [TdtSession] (who
 * am I / which client is signed in), [Store] (workspace names for the balloon title, and a poke to
 * refresh after a batch of notices so the Runs tab's `Runs · N` badge picks up the new arrival
 * without waiting for its own poll), [ApprovalNotifier] (the actual balloon) and [TdtSettings] (the
 * poll interval + the persisted 24h dedupe set in [TdtSettings.State.notifiedRuns]).
 *
 * A port of `services/vscode/src/extension.ts`'s approval wiring: built once, [rearm] on sign-in /
 * sign-out / profile-or-BU switch (via [TdtSessionListener]) and on a settings change (via
 * [TdtSettingsListener]) — both dispatched onto [scope] so a slow [ApprovalWatcher.prime] never
 * blocks the message-bus publisher. [markSeen] is the advisory dedupe hook the run-output tail uses
 * (see [com.terraducktel.jetbrains.session.TdtSessionStarter]) so its own "awaiting approval" toast
 * and this service's background poll never announce the same run twice.
 */
@Service(Service.Level.APP)
class ApprovalService(val scope: CoroutineScope) : Disposable {

    private object SettingsSeenStore : SeenStore {
        override fun get(): Map<String, Long> = TdtSettings.getInstance().state.notifiedRuns
        override fun set(v: Map<String, Long>) {
            TdtSettings.getInstance().state.notifiedRuns = v.toMutableMap()
        }
    }

    private val watcher = ApprovalWatcher(
        client = { TdtSession.getInstance().clientOrNull() },
        workspaceName = { id -> Store.getInstance().workspace(id)?.name ?: id.take(8) },
        notify = { notice ->
            ApprovalNotifier.show(activeProject(), notice)
            Store.getInstance().refresh()
        },
        seen = SettingsSeenStore,
        trace = { line -> if (TdtSettings.getInstance().state.trace) TdtLog.trace(line) },
        scope = scope,
    )

    /** Test seam: the real per-poll floor is 15s (see [intervalMs]) — far too slow to drive from a
     *  test. Overriding this down (e.g. to a few hundred ms) lets a test's `approvalsPollSeconds`
     *  setting translate into an actually-fast background poll instead of being floored back up. */
    internal var minIntervalMs: Long = 15_000L

    // Named distinctly from the public rearm() below (rather than just `rearm`) so there is no
    // property/function name overlap to reason about at call sites.
    private val rearmController = Rearm(
        key = ::rearmKey,
        prime = watcher::prime,
        start = { watcher.start(intervalMs()) },
        stop = watcher::stop,
    )

    init {
        val connection = ApplicationManager.getApplication().messageBus.connect(this)
        connection.subscribe(
            TdtSessionListener.TOPIC,
            object : TdtSessionListener {
                override fun sessionChanged() = rearmAsync()
            },
        )
        connection.subscribe(
            TdtSettingsListener.TOPIC,
            object : TdtSettingsListener {
                override fun settingsChanged() = rearmAsync()
            },
        )
        rearmAsync()
    }

    private fun rearmAsync() {
        scope.launch(Dispatchers.IO) { rearm() }
    }

    /** `"<profile>:<bu>"` while signed in, else null — [Rearm]'s prime-once-per-key guard against
     *  that same session (a profile/BU switch is a new key; signing out is no key at all). */
    private fun rearmKey(): String? {
        val session = TdtSession.getInstance()
        if (!session.isSignedIn()) return null
        val profile = session.profile ?: return null
        return "${profile.name}:${session.bu}"
    }

    /** `0` disables the poll entirely; any positive value is floored at 15s (see [minIntervalMs]
     *  for how a test overrides that floor). Mirrors the VS Code milestone-C contract verbatim. */
    private fun intervalMs(): Long {
        val seconds = TdtSettings.getInstance().state.approvalsPollSeconds
        if (seconds <= 0) return 0L
        return maxOf(minIntervalMs, seconds.toLong() * 1000L)
    }

    private fun activeProject(): Project? =
        ProjectUtil.getActiveProject() ?: ProjectManager.getInstance().openProjects.firstOrNull()

    /** Advisory dedupe: told about a run BEFORE the run-output tail's own "awaiting approval" toast
     *  goes up, so the background poll never announces the same run a second time. Blocking — call
     *  off the EDT (mirrors [ApprovalWatcher.markSeen]). */
    fun markSeen(runId: String) = watcher.markSeen(runId)

    /** Blocking: primes (if the session key changed) then (re)starts or stops the poll loop
     *  depending on [rearmKey] / [intervalMs]. Safe to call from any non-EDT thread; see [Rearm]. */
    fun rearm() = rearmController.invoke()

    /** Test seam: drives one poll synchronously off whatever thread the caller is on, exactly like
     *  [ApprovalWatcher.poll] — bypassing the real timer so a test doesn't have to wait on it. */
    internal fun pollNow() = watcher.poll()

    override fun dispose() {
        watcher.dispose()
    }

    /** Touches [getInstance] once per IDE run so this light service (and its [ApprovalWatcher]) is
     *  actually instantiated at startup — light services are lazy, so without this nothing would
     *  prime/poll until some other code happened to call [getInstance] first (e.g. [markSeen] from
     *  a run the user just started). Registered as a `backgroundPostStartupActivity` alongside
     *  [com.terraducktel.jetbrains.session.TdtSessionStarter] in `plugin.xml`. */
    class Starter : ProjectActivity {
        override suspend fun execute(project: Project) {
            ApprovalService.getInstance()
        }
    }

    companion object {
        fun getInstance(): ApprovalService = service()
    }
}
