package com.terraducktel.jetbrains.output

import com.intellij.execution.ui.ConsoleViewContentType
import com.intellij.openapi.editor.colors.TextAttributesKey
import com.terraducktel.jetbrains.ui.TdtTextAttributes

/** How [RunConsoles] prints each tail line: `── step [status]` / `── run status` headers bold and
 *  tinted by status (the design handoff's `run-output` tone rule), `✕` errors as error output,
 *  everything else as normal output. The text itself is unchanged. */
object RunOutputStyle {

    private val STEP_HEADER = Regex("""^── .* \[(\w+)]$""")
    private val RUN_HEADER = Regex("""^── run (\w+)$""")

    private val TYPES: Map<TextAttributesKey, ConsoleViewContentType> =
        listOf(
            TdtTextAttributes.HEADER_OK, TdtTextAttributes.HEADER_RUN, TdtTextAttributes.HEADER_AWAITING,
            TdtTextAttributes.HEADER_FAILED, TdtTextAttributes.HEADER_MUTED,
        ).associateWith { ConsoleViewContentType(it.externalName, it) }

    /** Colour key for a header line, or null when [line] isn't a header. */
    fun headerKey(line: String): TextAttributesKey? {
        val status = (RUN_HEADER.find(line) ?: STEP_HEADER.find(line))?.groupValues?.get(1) ?: return null
        return when {
            "fail" in status || "cancel" in status -> TdtTextAttributes.HEADER_FAILED
            status in setOf("success", "applied", "planned") -> TdtTextAttributes.HEADER_OK
            "await" in status -> TdtTextAttributes.HEADER_AWAITING
            status in setOf("running", "pending", "planning", "applying") -> TdtTextAttributes.HEADER_RUN
            else -> TdtTextAttributes.HEADER_MUTED
        }
    }

    fun typeFor(key: TextAttributesKey): ConsoleViewContentType = TYPES.getValue(key)

    fun contentTypeFor(line: String): ConsoleViewContentType = when {
        line.startsWith("✕") -> ConsoleViewContentType.ERROR_OUTPUT
        else -> headerKey(line)?.let(::typeFor) ?: ConsoleViewContentType.NORMAL_OUTPUT
    }
}
