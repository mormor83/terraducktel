package com.terraducktel.jetbrains.editor

import com.intellij.ide.BrowserUtil
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
import com.intellij.openapi.util.Disposer
import com.intellij.openapi.wm.WindowManager
import com.terraducktel.jetbrains.TdtLog
import com.terraducktel.jetbrains.actions.ActionUtil
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
import java.util.concurrent.CopyOnWriteArrayList
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

    private val seq = AtomicInteger(0)
    private val gitProbe = GitProbe()
    private val listeners = CopyOnWriteArrayList<() -> Unit>()

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
                override fun settingsChanged() = refresh()
            },
        )
        Store.getInstance().addListener(this) { refresh() }
        refresh()
    }

    /** Fires [l] after every published change (whether it ends up hiding the item or not);
     *  removed automatically when [parent] is disposed. Mirrors [Store.addListener]. */
    fun addListener(parent: Disposable, l: () -> Unit) {
        listeners += l
        Disposer.register(parent) { listeners -= l }
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
            hide()
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
        show(cur, git)
    }

    /** Disabled / no active Terraform file / signed out — the widget disappears entirely. */
    private fun hide() {
        current = null
        view = null
        fireListeners()
        updateWidgetOnEdt()
    }

    /** A tf file is active and the gate passed — always publishes a visible item: either
     *  [StatusText.mapped] ([cur] non-null) or [StatusText.unmapped] (no workspace claims it, but
     *  the item still shows so the user can act on it). */
    private fun show(cur: CurrentFile?, git: GitInfo?) {
        current = cur
        view = if (cur != null) StatusText.mapped(cur, Store.getInstance().runsFor(cur.ws.id).firstOrNull()) else StatusText.unmapped(git)
        fireListeners()
        updateWidgetOnEdt()
    }

    private fun fireListeners() {
        for (l in listeners) {
            try {
                l()
            } catch (e: CancellationException) {
                throw e
            } catch (t: Throwable) {
                if (t is ControlFlowException) throw t
                TdtLog.LOG.warn("Terraducktel: an EditorStatus listener threw", t)
            }
        }
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
                        showBranchChoice(branch, ws.repo_ref) { chosen -> RunActions.trigger(project, ws, "plan", chosen) }
                    } else {
                        RunActions.trigger(project, ws, "plan", null)
                    }
                },
                ModalityState.any(),
            ) { project.isDisposed }
        }
    }

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

    /** The status-bar item's click popup — a port of `status.ts`'s `actions()`. When [current] is
     *  null every item collapses to just "Open in browser" (mirroring `status.ts`, which skips the
     *  quick pick entirely and opens the browser directly in that case). */
    fun actionsPopup(): ListPopup {
        data class Item(val label: String, val act: () -> Unit)
        val cur = current
        val items = if (cur != null) {
            val last = Store.getInstance().runsFor(cur.ws.id).firstOrNull()
            buildList {
                add(Item("Plan this leaf") { planCurrent() })
                if (last != null) add(Item("Show last plan") { PlanDocument.open(project, last.id, cur.ws.name) })
                add(Item("Reveal in tool window") { TdtToolWindowFactory.revealWorkspace(project, cur.ws.id) })
                add(Item("Open in browser") { openInBrowser() })
            }
        } else {
            listOf(Item("Open in browser") { openInBrowser() })
        }
        val title = cur?.let { "${it.ws.name} · ${it.ws.tf_working_dir}" }
        val step = object : BaseListPopupStep<Item>(title, items) {
            override fun getTextFor(value: Item): String = value.label
            override fun onChosen(selectedValue: Item, finalChoice: Boolean): PopupStep<*>? =
                doFinalStep { selectedValue.act() }
        }
        return JBPopupFactory.getInstance().createListPopup(step)
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
