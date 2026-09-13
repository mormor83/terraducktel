package com.terraducktel.jetbrains.editor

import com.intellij.ide.BrowserUtil
import com.intellij.notification.NotificationType
import com.intellij.openapi.Disposable
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.application.ModalityState
import com.intellij.openapi.components.Service
import com.intellij.openapi.components.service
import com.intellij.openapi.diagnostic.ControlFlowException
import com.intellij.openapi.fileEditor.FileEditorManager
import com.intellij.openapi.fileEditor.FileEditorManagerEvent
import com.intellij.openapi.fileEditor.FileEditorManagerListener
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.popup.JBPopupFactory
import com.intellij.openapi.ui.popup.ListPopup
import com.intellij.openapi.ui.popup.PopupStep
import com.intellij.openapi.ui.popup.util.BaseListPopupStep
import com.intellij.openapi.wm.StatusBarWidgetFactory
import com.intellij.openapi.wm.WindowManager
import com.intellij.openapi.wm.impl.status.widget.StatusBarWidgetsManager
import com.terraducktel.jetbrains.TdtLog
import com.terraducktel.jetbrains.actions.ActionUtil
import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.api.Workspace
import com.terraducktel.jetbrains.output.PlanDocument
import com.terraducktel.jetbrains.output.RunActions
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.session.TdtSessionListener
import com.terraducktel.jetbrains.settings.TdtSettings
import com.terraducktel.jetbrains.settings.TdtSettingsListener
import com.terraducktel.jetbrains.state.Store
import com.terraducktel.jetbrains.toolwindow.TdtToolWindowFactory
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.io.File
import java.util.concurrent.atomic.AtomicInteger

private val TF_EXTENSIONS = setOf("tf", "tfvars", "hcl")

/** The workspace the active file currently resolves to — a port of `status.ts`'s `CurrentFile`. */
data class CurrentFile(val ws: Workspace, val git: GitInfo?, val exact: Boolean, val resolvedPath: String)

/**
 * Tracks which Terraducktel workspace (if any) the active editor's file belongs to — a coroutine
 * port of `services/vscode/src/editor/status.ts`'s `EditorStatus` class, minus the VS Code
 * version's own `StatusBarItem` (that's [TdtStatusBarWidget] here).
 *
 * [refresh] runs off the EDT (it may shell out to `git`) and is guarded by [seq] the same way
 * `status.ts` guards its own `refresh()`: a call superseded by a later one bails out before ever
 * publishing its (by-then-stale) result. [current]/[view] are published together; `view == null`
 * means "hide the status-bar item entirely" (disabled, no active Terraform file, or signed out) —
 * as opposed to a non-null [StatusText.View] with [StatusText.unmapped]'s text, which means "show
 * the item, but say nothing here is imported".
 */
@Service(Service.Level.PROJECT)
class EditorStatus(private val project: Project, private val scope: CoroutineScope) : Disposable {

    @Volatile var current: CurrentFile? = null
        private set

    @Volatile var view: StatusText.View? = null
        private set

    /** Test seam: sets [view] directly, bypassing [refresh]'s whole file/git/workspace resolution
     *  pipeline — for a widget test that just needs a known [StatusText.View] to render (or none),
     *  not a real editor/checkout round trip. Never touches [current] or fires the widget-update
     *  side effects [show]/[hide] do; a test reads the widget's own getters straight afterward. */
    internal fun setViewForTest(v: StatusText.View?) { view = v }

    private val seq = AtomicInteger(0)
    private val gitProbe = GitProbe()

    init {
        val projectConnection = project.messageBus.connect(this)
        projectConnection.subscribe(
            FileEditorManagerListener.FILE_EDITOR_MANAGER,
            object : FileEditorManagerListener {
                override fun selectionChanged(event: FileEditorManagerEvent) = refresh()
            },
        )
        val appConnection = ApplicationManager.getApplication().messageBus.connect(this)
        appConnection.subscribe(
            TdtSessionListener.TOPIC,
            object : TdtSessionListener {
                override fun sessionChanged() = refresh()
            },
        )
        appConnection.subscribe(
            TdtSettingsListener.TOPIC,
            object : TdtSettingsListener {
                override fun settingsChanged() {
                    refresh()
                    refreshWidgetAvailability()
                }
            },
        )
        Store.getInstance().addListener(this) { refresh() }
        refresh()
    }

    /** Re-resolves the active file to a workspace. Safe to call from any thread; the actual work
     *  (git shell-out, mapping) runs on [Dispatchers.IO]. A call superseded by a later one before
     *  it gets a chance to publish is simply dropped. */
    fun refresh() {
        val my = seq.incrementAndGet()
        scope.launch(Dispatchers.IO) {
            try {
                doRefresh(my)
            } catch (e: CancellationException) {
                throw e
            } catch (t: Throwable) {
                // ControlFlowException (e.g. ProcessCanceledException) is a marker interface, not
                // itself a Throwable subtype, so it can't be its own catch clause — see Store.doRefresh.
                if (t is ControlFlowException) throw t
                TdtLog.LOG.warn("Terraducktel: EditorStatus.refresh() failed", t)
            }
        }
    }

    private fun isCurrent(my: Int) = my == seq.get()

    private fun doRefresh(my: Int) {
        val enabled = TdtSettings.getInstance().state.statusBarEnabled
        val file = FileEditorManager.getInstance(project).selectedFiles.firstOrNull()
        val isTfFile = file != null && file.isInLocalFileSystem && file.extension?.lowercase() in TF_EXTENSIONS
        val signedIn = TdtSession.getInstance().isSignedIn()
        if (!enabled || !isTfFile || !signedIn) {
            if (!isCurrent(my)) return
            hide(my)
            return
        }

        val rawPath = file.path
        val resolvedPath = resolveRealPath(rawPath)
        if (!isCurrent(my)) return

        val git = gitProbe.info(resolvedPath)
        if (!isCurrent(my)) return

        val match = git?.let { g ->
            Mapping.relativeDir(g.root, resolvedPath)?.let { rel ->
                Mapping.matchWorkspace(Store.getInstance().workspaces, rel, g.remoteUrl)
            }
        }
        val cur = match?.let { CurrentFile(it.ws, git, it.exact, resolvedPath) }
        show(my, cur, git)
    }

    /** Disabled / no active Terraform file / signed out — the widget disappears entirely.
     *  [my] is re-checked immediately before the [current]/[view] assignment (not just earlier in
     *  [doRefresh]): two IO-dispatcher threads can genuinely run concurrently, so a call that
     *  passed its check further up could still be descheduled right up until this point and let a
     *  newer, already-published refresh be overwritten with stale content. */
    private fun hide(my: Int) {
        if (!isCurrent(my)) return
        current = null
        view = null
        updateWidgetOnEdt()
    }

    /** A tf file is active and the gate passed — always publishes a visible item: either
     *  [StatusText.mapped] ([cur] non-null) or [StatusText.unmapped] (no workspace claims it, but
     *  the item still shows so the user can act on it). [my] is re-checked immediately before the
     *  assignment — see [hide]'s doc for why that final check (not just the earlier ones in
     *  [doRefresh]) matters. */
    private fun show(my: Int, cur: CurrentFile?, git: GitInfo?) {
        if (!isCurrent(my)) return
        current = cur
        view = if (cur != null) StatusText.mapped(cur, Store.getInstance().runsFor(cur.ws.id).firstOrNull()) else StatusText.unmapped(git)
        updateWidgetOnEdt()
    }

    /** Test seam: how [refreshWidgetAvailability] asks the platform to re-evaluate one factory's
     *  availability — defaults to the real [StatusBarWidgetsManager] call. A light test fixture's
     *  status bar is a dummy that never actually (re)creates widgets, so a test can't observe the
     *  real call's effect; swapping this in lets it instead assert both factory classes were asked. */
    internal var widgetRefresher: (Class<out StatusBarWidgetFactory>) -> Unit = { factoryClass ->
        project.service<StatusBarWidgetsManager>().updateWidget(factoryClass)
    }

    /** Re-evaluates whether each status-bar widget factory should be enabled/disabled — a settings
     *  change (e.g. adding the FIRST profile, or re-enabling "Show status bar item") flips
     *  [StatusBarWidgetFactory.isAvailable][com.intellij.openapi.wm.StatusBarWidgetFactory
     *  .isAvailable], but nothing re-asks the platform about that on its own; a plain
     *  `StatusBar.updateWidget` (used by [updateWidgetOnEdt] for content changes) only refreshes an
     *  ALREADY-shown widget's text/tooltip, it does not add or remove one. Without this, a fresh
     *  profile added after startup would need an IDE restart before its widget appeared. */
    private fun refreshWidgetAvailability() {
        ApplicationManager.getApplication().invokeLater(
            {
                widgetRefresher(TdtStatusBarWidgetFactory::class.java)
                widgetRefresher(ProfileStatusBarWidgetFactory::class.java)
            },
            ModalityState.any(),
        ) { project.isDisposed }
    }

    private fun updateWidgetOnEdt() {
        ApplicationManager.getApplication().invokeLater(
            {
                WindowManager.getInstance().getStatusBar(project)?.updateWidget(TdtStatusBarWidgetFactory.ID)
            },
            ModalityState.any(),
        ) { project.isDisposed }
    }

    /** Port of `status.ts`'s `planCurrent()`: re-probes git fresh (a terminal `git checkout`
     *  fires no editor event, so [current]'s cached [GitInfo] can be stale — and a stale branch
     *  here would pin the WRONG branch server-side) before deciding whether to ask which branch to
     *  plan on. Safe to call from the EDT; the git re-probe and the actual trigger both hop off/
     *  onto the EDT as needed via [ActionUtil.runBackground]. */
    fun planCurrent() {
        val cur = current
        if (cur == null) {
            ActionUtil.notify(project, "Terraducktel: the active file is not inside an imported workspace.")
            return
        }
        val ws = cur.ws
        ActionUtil.runBackground(project, "TDT: preparing plan…") {
            var git = cur.git
            if (git != null) {
                gitProbe.invalidate(git.root)
                git = gitProbe.info(cur.resolvedPath) ?: git
            }
            val branch = git?.branch
            ApplicationManager.getApplication().invokeLater(
                {
                    if (branch != null && branch != ws.repo_ref) {
                        branchChooser(branch, ws.repo_ref) { chosen -> triggerPlanSafely(ws, chosen) }
                    } else {
                        triggerPlanSafely(ws, null)
                    }
                },
                ModalityState.any(),
            ) { project.isDisposed }
        }
    }

    /** Test seam for "which branch to plan on" once [planCurrent] has determined the checked-out
     *  branch differs from the workspace's tracked one — production default shows the real
     *  two-item popup ([showBranchChoice]); tests swap this in to bypass Swing and choose
     *  deterministically. [onChosen] receives the branch to pin to, or null to plan on the
     *  workspace's already-tracked branch (no pin). */
    internal var branchChooser: (branch: String, repoRef: String, onChosen: (String?) -> Unit) -> Unit =
        { branch, repoRef, onChosen -> showBranchChoice(branch, repoRef, onChosen) }

    private fun showBranchChoice(branch: String, repoRef: String, onChosen: (String?) -> Unit) {
        data class Item(val label: String, val branch: String?)
        val items = listOf(
            Item("Plan on $branch (pins the workspace)", branch),
            Item("Plan on $repoRef", null),
        )
        val step = object : BaseListPopupStep<Item>("Checked out $branch, workspace tracks $repoRef", items) {
            override fun getTextFor(value: Item): String = value.label
            override fun onChosen(selectedValue: Item, finalChoice: Boolean): PopupStep<*>? =
                doFinalStep { onChosen(selectedValue.branch) }
        }
        JBPopupFactory.getInstance().createListPopup(step).showCenteredInCurrentWindow(project)
    }

    /** [RunActions.trigger] itself must run on the EDT (it shows the Apply/Destroy confirmation
     *  dialogs synchronously); wrapping it here — rather than leaving a raw throw to reach the
     *  platform as an uncaught exception — mirrors `status.ts`'s `pick.act().catch(...)`. */
    private fun triggerPlanSafely(ws: Workspace, branch: String?) = safely { RunActions.trigger(project, ws, "plan", branch) }

    private fun safely(action: () -> Unit) {
        try {
            action()
        } catch (e: CancellationException) {
            throw e
        } catch (t: Throwable) {
            if (t is ControlFlowException) throw t
            ActionUtil.notify(project, "Terraducktel: ${t.message ?: "an unexpected error occurred"}", NotificationType.ERROR)
        }
    }

    /** The status-bar item's click popup — a port of `status.ts`'s `actions()`. When [current] is
     *  null every item collapses to just "Open in browser" (mirroring `status.ts`, which skips the
     *  quick pick entirely and opens the browser directly in that case). Each item's action is
     *  wrapped so a throw surfaces as an error balloon (mirrors `status.ts`'s `pick.act().catch`)
     *  instead of reaching the platform as a raw, unhandled exception. */
    fun actionsPopup(): ListPopup {
        data class Item(val label: String, val act: () -> Unit)
        val cur = current
        val last = cur?.let { Store.getInstance().runsFor(it.ws.id).firstOrNull() }
        val items = popupItems(cur, last).map { label ->
            Item(label) {
                when (label) {
                    "Plan this leaf" -> planCurrent()
                    "Show last plan" -> PlanDocument.open(project, last!!.id, cur.ws.name)
                    "Reveal in tool window" -> TdtToolWindowFactory.revealWorkspace(project, cur!!.ws.id)
                    "Open in browser" -> openInBrowser()
                }
            }
        }
        val step = object : BaseListPopupStep<Item>(popupTitle(cur), items) {
            override fun getTextFor(value: Item): String = value.label
            override fun onChosen(selectedValue: Item, finalChoice: Boolean): PopupStep<*>? =
                doFinalStep { safely(selectedValue.act) }
        }
        return JBPopupFactory.getInstance().createListPopup(step)
    }

    /** Pure decision behind [actionsPopup]'s item list — labels only, no actions bound — so a test
     *  can assert exactly which items show up for a given (mapped/unmapped, has-a-last-run) state
     *  without driving a real Swing popup. Mirrors `status.ts`'s `actions()` branching: unmapped
     *  collapses to the single "Open in browser" item; "Show last plan" only when [lastRun] is
     *  non-null. */
    internal fun popupItems(cur: CurrentFile?, lastRun: Run?): List<String> {
        if (cur == null) return listOf("Open in browser")
        return buildList {
            add("Plan this leaf")
            if (lastRun != null) add("Show last plan")
            add("Reveal in tool window")
            add("Open in browser")
        }
    }

    /** Pure decision behind [actionsPopup]'s title — same placeholder shape as `status.ts`'s
     *  `showQuickPick`: `<name> · <tf_working_dir>[ · branch <branch>]`; null when unmapped. */
    internal fun popupTitle(cur: CurrentFile?): String? = cur?.let {
        val branchSuffix = it.git?.branch?.let { b -> " · branch $b" } ?: ""
        "${it.ws.name} · ${it.ws.tf_working_dir}$branchSuffix"
    }

    private fun openInBrowser() {
        val ui = TdtSession.getInstance().uiUrl() ?: return
        BrowserUtil.browse("$ui/")
    }

    override fun dispose() {}

    companion object {
        fun getInstance(project: Project): EditorStatus = project.service()

        /** Symlinked checkouts (a `~/code` symlink into another volume, a bind mount, …) can make
         *  the editor's path and `git rev-parse --show-toplevel`'s realpath output disagree on the
         *  prefix, which makes [Mapping.relativeDir] fail to strip the root — port of `status.ts`'s
         *  `resolvePath`. Never throws; falls back to the raw path. */
        internal fun resolveRealPath(path: String): String =
            runCatching { File(path).toPath().toRealPath().toString() }.getOrDefault(path)
    }
}
