package com.terraducktel.jetbrains.output

import com.intellij.execution.filters.TextConsoleBuilderFactory
import com.intellij.execution.ui.ConsoleView
import com.intellij.execution.ui.ConsoleViewContentType
import com.intellij.notification.NotificationType
import com.intellij.openapi.Disposable
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.application.ModalityState
import com.intellij.openapi.components.Service
import com.intellij.openapi.components.service
import com.intellij.openapi.progress.ProgressIndicator
import com.intellij.openapi.progress.ProgressManager
import com.intellij.openapi.progress.Task
import com.intellij.openapi.project.Project
import com.intellij.openapi.wm.ToolWindow
import com.intellij.openapi.wm.ToolWindowManager
import com.intellij.ui.content.Content
import com.intellij.ui.content.ContentFactory
import com.intellij.ui.content.ContentManagerEvent
import com.intellij.ui.content.ContentManagerListener
import com.intellij.util.concurrency.ThreadingAssertions
import com.terraducktel.jetbrains.TdtLog
import com.terraducktel.jetbrains.actions.ActionUtil
import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.session.TdtSession
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Project service that opens/reveals one console tab per run being followed, inside the
 * "Terraducktel" tool window, and runs [RunTail.tail] in a cancellable background task feeding
 * it. A port of `services/vscode/src/output/runOutput.ts`'s `RunOutputManager`, with a JetBrains
 * `ConsoleView` + `Content` standing in for a VS Code `OutputChannel`.
 */
@Service(Service.Level.PROJECT)
class RunConsoles(private val project: Project) : Disposable {

    private class Entry(val content: Content, val console: ConsoleView) {
        val active = AtomicBoolean(true)
        val cancelled = AtomicBoolean(false)
    }

    private val entries = ConcurrentHashMap<String, Entry>()
    private val listenerRegistered = AtomicBoolean(false)

    /** Every EDT hop below (console printing, `onLanded`) uses [ModalityState.any] plus this
     *  "expired" condition: without it, output queued via the default NON_MODAL state freezes
     *  behind any modal dialog — including this plugin's own Apply/Destroy/Approve prompts — and
     *  then dumps all at once when the dialog closes. */
    private fun expired() = project.isDisposed

    /** Opens (or reveals) a console tab for [runId] and starts following it, unless a follow for
     *  this run is already active — in which case the existing tab is just revealed. Must be
     *  called on the EDT. */
    fun watch(runId: String, title: String, onLanded: ((Run) -> Unit)? = null) {
        ThreadingAssertions.assertEventDispatchThread()
        val toolWindow = ToolWindowManager.getInstance(project).getToolWindow("Terraducktel")
        if (toolWindow == null) {
            TdtLog.LOG.warn("Terraducktel: tool window unavailable — cannot watch run $runId")
            ActionUtil.notify(project, "TDT: the Terraducktel tool window is unavailable — cannot follow this run.", NotificationType.ERROR)
            return
        }
        ensureContentListener(toolWindow)

        val existing = entries[runId]
        if (existing != null) {
            toolWindow.contentManager.setSelectedContent(existing.content)
            toolWindow.show()
            if (existing.active.get()) return
            // Re-attaching to a run we already followed (after a cancel, say): keep what was
            // printed — it is the only record of the first half of the run — and mark where
            // following resumed.
            existing.console.print("─".repeat(20) + " re-attached " + "─".repeat(20) + "\n", ConsoleViewContentType.NORMAL_OUTPUT)
            existing.cancelled.set(false)
            existing.active.set(true)
            startTail(runId, title, existing, onLanded)
            return
        }

        val console = TextConsoleBuilderFactory.getInstance().createBuilder(project).console
        console.clear()
        val content = ContentFactory.getInstance().createContent(console.component, "Run ${runId.take(8)} · $title", false).apply {
            isCloseable = true
            setDisposer(console)
        }
        val entry = Entry(content, console)
        entries[runId] = entry
        toolWindow.contentManager.addContent(content)
        toolWindow.contentManager.setSelectedContent(content)
        toolWindow.show()
        startTail(runId, title, entry, onLanded)
    }

    private fun startTail(runId: String, title: String, entry: Entry, onLanded: ((Run) -> Unit)?) {
        ProgressManager.getInstance().run(object : Task.Backgroundable(project, "TDT: watching $title — cancel to stop following", true) {
            override fun run(indicator: ProgressIndicator) {
                // Same flag either way: an explicit dispose()/content-close (entry.cancelled) and
                // this task's own cancel button must stop the loop identically.
                val cancelled = { indicator.isCanceled || entry.cancelled.get() }
                val sink = LineSink { line ->
                    // ModalityState.any() + the disposal condition: printing must not queue up
                    // behind a modal dialog (see the field doc on `expired()`), and must never
                    // fire after the project is gone.
                    ApplicationManager.getApplication().invokeLater(
                        {
                            val type = if (line.startsWith("✕")) ConsoleViewContentType.ERROR_OUTPUT else ConsoleViewContentType.NORMAL_OUTPUT
                            entry.console.print("$line\n", type)
                        },
                        ModalityState.any(),
                    ) { expired() }
                }
                try {
                    val client = TdtSession.getInstance().requireClient()
                    val run = RunTail.tail(client, runId, sink, isCancelled = cancelled)
                    entry.active.set(false)
                    if (!cancelled()) {
                        onLanded?.let { cb ->
                            ApplicationManager.getApplication().invokeLater({ cb(run) }, ModalityState.any()) { expired() }
                        }
                    }
                } catch (e: Exception) {
                    entry.active.set(false)
                    // Once cancelled, the content may already be gone (dispose()/content-removal
                    // cancels every entry before it can be torn down) — never touch it after that.
                    if (!cancelled()) sink.appendLine("✕ ${e.message}")
                }
            }
        })
    }

    /** Removes an entry (and cancels its tail) when the user closes its Content tab. Registered
     *  once per tool window instance — idempotent across repeated [watch] calls. */
    private fun ensureContentListener(toolWindow: ToolWindow) {
        if (!listenerRegistered.compareAndSet(false, true)) return
        toolWindow.contentManager.addContentManagerListener(object : ContentManagerListener {
            override fun contentRemoved(event: ContentManagerEvent) {
                val removed = event.content
                val id = entries.entries.find { it.value.content === removed }?.key ?: return
                entries.remove(id)?.cancelled?.set(true)
            }
        })
    }

    /** Cancels every active tail, then removes each Content from the tool window (which triggers
     *  its `setDisposer(console)` disposer) — otherwise a tail's already-queued print could land on
     *  a console nobody owns anymore between this service's disposal and the content manager's own
     *  teardown. Guards for a missing tool window or an already-disposed project (both routine
     *  during project close). */
    override fun dispose() {
        for (entry in entries.values) entry.cancelled.set(true)
        if (!project.isDisposed) {
            val toolWindow = ToolWindowManager.getInstance(project).getToolWindow("Terraducktel")
            if (toolWindow != null) {
                for (entry in entries.values.toList()) {
                    toolWindow.contentManager.removeContent(entry.content, true)
                }
            }
        }
        entries.clear()
    }

    companion object {
        fun getInstance(project: Project): RunConsoles = project.service()
    }
}
