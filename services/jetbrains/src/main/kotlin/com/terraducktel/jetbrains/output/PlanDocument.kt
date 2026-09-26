package com.terraducktel.jetbrains.output

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.application.ModalityState
import com.intellij.openapi.editor.Editor
import com.intellij.openapi.editor.colors.TextAttributesKey
import com.intellij.openapi.editor.markup.HighlighterLayer
import com.intellij.openapi.editor.markup.LineMarkerRenderer
import com.intellij.openapi.fileEditor.FileEditor
import com.intellij.openapi.fileEditor.FileEditorManager
import com.intellij.openapi.fileEditor.TextEditor
import com.intellij.openapi.fileTypes.FileTypeManager
import com.intellij.openapi.fileTypes.PlainTextFileType
import com.intellij.openapi.fileTypes.UnknownFileType
import com.intellij.openapi.project.Project
import com.intellij.openapi.util.TextRange
import com.intellij.testFramework.LightVirtualFile
import com.intellij.util.ui.JBUI
import com.terraducktel.jetbrains.actions.ActionUtil
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.ui.TdtColors
import com.terraducktel.jetbrains.ui.TdtTextAttributes
import java.awt.Graphics
import java.awt.Rectangle

/** The kinds of terraform plan output line a [PlanDocument] decorates — a port of
 *  `services/vscode/src/output/planDocument.ts`'s `LineKind`. */
enum class PlanLineKind { ADD, DELETE, CHANGE, REPLACE, NONE }

/**
 * Opens a run's `terraform plan` output as a read-only, syntax-highlighted (best-effort HCL via
 * the "tf" file type), line-decorated scratch document — a port of `services/vscode/src/output/
 * planDocument.ts`'s `PlanDocumentProvider`, minus the VS Code version's URI scheme/content
 * provider machinery (a JetBrains [LightVirtualFile] plays that role here).
 */
object PlanDocument {

    /** Pure port of `planDocument.ts`'s `planLineKinds` line classifier. Leading whitespace before
     *  the marker is allowed. The two-character `-/+`/`+/-` replace marker classifies as [PlanLineKind.REPLACE] whether or
     *  not it's followed by a space (matching `planDocument.ts`'s `startsWith("-/+")`); the
     *  single-character `+`/`-`/`~` markers must be followed by a space, so a bare `-`/`+` (as can
     *  appear alone in a plan's closing summary) classifies as [PlanLineKind.NONE], not
     *  [PlanLineKind.DELETE]/[PlanLineKind.ADD]. */
    fun classifyLine(line: String): PlanLineKind {
        val t = line.trimStart()
        return when {
            t.startsWith("-/+") || t.startsWith("+/-") -> PlanLineKind.REPLACE
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
     *  [TextEditor] — adds one line highlighter per `+`/`-`/`~`/`-/+` line via [classifyLine] (replace
     *  lines also get a 2px bar in the replace colour at the left edge of the gutter). Must be
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
                val kind = classifyLine(document.getText(range))
                val key = keyFor(kind) ?: continue
                val highlighter = markup.addLineHighlighter(key, line, HighlighterLayer.ADDITIONAL_SYNTAX)
                if (kind == PlanLineKind.REPLACE) highlighter.lineMarkerRenderer = ReplaceBar
            }
        }
        return editor
    }

    private fun keyFor(kind: PlanLineKind): TextAttributesKey? = when (kind) {
        PlanLineKind.ADD -> TdtTextAttributes.PLAN_ADD
        PlanLineKind.DELETE -> TdtTextAttributes.PLAN_DESTROY
        PlanLineKind.CHANGE -> TdtTextAttributes.PLAN_CHANGE
        PlanLineKind.REPLACE -> TdtTextAttributes.PLAN_REPLACE
        PlanLineKind.NONE -> null
    }

    /** The design's "2px left border in the replace colour" — line highlighters can't draw
     *  borders, so it is painted as a gutter line marker. */
    private object ReplaceBar : LineMarkerRenderer {
        override fun paint(editor: Editor, g: Graphics, r: Rectangle) {
            g.color = TdtColors.REPLACE
            g.fillRect(r.x, r.y, JBUI.scale(2), r.height)
        }
    }
}
