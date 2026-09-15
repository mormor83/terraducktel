package com.terraducktel.jetbrains.output

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.application.ModalityState
import com.intellij.openapi.diff.DiffColors
import com.intellij.openapi.editor.colors.TextAttributesKey
import com.intellij.openapi.editor.markup.HighlighterLayer
import com.intellij.openapi.fileEditor.FileEditor
import com.intellij.openapi.fileEditor.FileEditorManager
import com.intellij.openapi.fileEditor.TextEditor
import com.intellij.openapi.fileTypes.FileTypeManager
import com.intellij.openapi.fileTypes.PlainTextFileType
import com.intellij.openapi.fileTypes.UnknownFileType
import com.intellij.openapi.project.Project
import com.intellij.openapi.util.TextRange
import com.intellij.testFramework.LightVirtualFile
import com.terraducktel.jetbrains.actions.ActionUtil
import com.terraducktel.jetbrains.session.TdtSession

/** The four kinds of terraform plan output line a [PlanDocument] cares about — a port of
 *  `services/vscode/src/output/planDocument.ts`'s `LineKind`, minus the VS Code version's separate
 *  "replace" bucket: `-/+`/`+/-` lines fold into [CHANGE] here since there is no fourth
 *  [com.intellij.openapi.diff.DiffColors] key to give them their own color. */
enum class PlanLineKind { ADD, DELETE, CHANGE, NONE }

/**
 * Opens a run's `terraform plan` output as a read-only, syntax-highlighted (best-effort HCL via
 * the "tf" file type), line-decorated scratch document — a port of `services/vscode/src/output/
 * planDocument.ts`'s `PlanDocumentProvider`, minus the VS Code version's URI scheme/content
 * provider machinery (a JetBrains [LightVirtualFile] plays that role here).
 */
object PlanDocument {

    /** Pure port of `planDocument.ts`'s `planLineKinds` line classifier (folding "replace" into
     *  [PlanLineKind.CHANGE] — see the enum doc). Leading whitespace before the marker is allowed.
     *  The two-character `-/+`/`+/-` replace marker classifies as [PlanLineKind.CHANGE] whether or
     *  not it's followed by a space (matching `planDocument.ts`'s `startsWith("-/+")`); the
     *  single-character `+`/`-`/`~` markers must be followed by a space, so a bare `-`/`+` (as can
     *  appear alone in a plan's closing summary) classifies as [PlanLineKind.NONE], not
     *  [PlanLineKind.DELETE]/[PlanLineKind.ADD]. */
    fun classifyLine(line: String): PlanLineKind {
        val t = line.trimStart()
        return when {
            t.startsWith("-/+") || t.startsWith("+/-") -> PlanLineKind.CHANGE
            t.startsWith("+ ") -> PlanLineKind.ADD
            t.startsWith("- ") -> PlanLineKind.DELETE
            t.startsWith("~ ") -> PlanLineKind.CHANGE
            else -> PlanLineKind.NONE
        }
    }

    fun fileNameFor(label: String, runId: String): String = "$label-${runId.take(8)}.tfplan.txt"

    /** Fetches the plan output for [runId] on a background task, then opens it on the EDT. Must be
     *  called on the EDT (starts a background task itself); the network call never runs on the EDT. */
    fun open(project: Project, runId: String, label: String) {
        ActionUtil.runBackground(project, "TDT: loading plan…") {
            val client = TdtSession.getInstance().requireClient()
            val text = client.getPlan(runId).plan_output ?: "(no plan output)"
            // A disposed-project guard: a project can close while this background fetch is in
            // flight, and FileEditorManager.getInstance(project) below must never run against a
            // dead project.
            ApplicationManager.getApplication().invokeLater(
                { openText(project, fileNameFor(label, runId), text) },
                ModalityState.nonModal(),
            ) { project.isDisposed }
        }
    }

    /** Builds a read-only [LightVirtualFile] named [name] holding [text], opens it, and — for a
     *  [TextEditor] — adds one line highlighter per `+`/`-`/`~` line via [classifyLine]. Must be
     *  called on the EDT. Returns the opened editor (or null if none opened) so a test can assert
     *  on the highlighter count. */
    internal fun openText(project: Project, name: String, text: String): FileEditor? {
        val byExtension = FileTypeManager.getInstance().getFileTypeByExtension("tf")
        val fileType = if (byExtension is UnknownFileType || byExtension == PlainTextFileType.INSTANCE) {
            PlainTextFileType.INSTANCE
        } else {
            byExtension
        }
        val vf = LightVirtualFile(name, fileType, text)
        vf.isWritable = false

        val editors = FileEditorManager.getInstance(project).openFile(vf, true)
        val editor = editors.firstOrNull() ?: return null
        if (editor is TextEditor) {
            val document = editor.editor.document
            val markup = editor.editor.markupModel
            for (line in 0 until document.lineCount) {
                val range = TextRange(document.getLineStartOffset(line), document.getLineEndOffset(line))
                val key = keyFor(classifyLine(document.getText(range))) ?: continue
                markup.addLineHighlighter(key, line, HighlighterLayer.ADDITIONAL_SYNTAX)
            }
        }
        return editor
    }

    private fun keyFor(kind: PlanLineKind): TextAttributesKey? = when (kind) {
        PlanLineKind.ADD -> DiffColors.DIFF_INSERTED
        PlanLineKind.DELETE -> DiffColors.DIFF_DELETED
        PlanLineKind.CHANGE -> DiffColors.DIFF_MODIFIED
        PlanLineKind.NONE -> null
    }
}
