package com.terraducktel.jetbrains.ui

import com.intellij.openapi.editor.colors.TextAttributesKey
import com.intellij.openapi.editor.markup.TextAttributes
import java.awt.Color
import java.awt.Font

/**
 * Colour-scheme keys for the plan document's diff lines and the run console's `── step [status]`
 * headers. Defaults come from [TdtColors]; users can override them under Settings → Editor →
 * Color Scheme → Terraducktel ([TdtColorSettingsPage]).
 */
object TdtTextAttributes {

    // The (name, TextAttributes) overload is the only way to give a plugin key brand defaults
    // without duplicating TdtColors into per-scheme XML; JBColor keeps them theme-aware.
    @Suppress("DEPRECATION")
    private fun key(name: String, fg: Color, bg: Color? = null, bold: Boolean = false): TextAttributesKey =
        TextAttributesKey.createTextAttributesKey(name, TextAttributes(fg, bg, null, null, if (bold) Font.BOLD else Font.PLAIN))

    val PLAN_ADD = key("TERRADUCKTEL_PLAN_ADD", TdtColors.ADD, TdtColors.ADD_BACKGROUND)
    val PLAN_CHANGE = key("TERRADUCKTEL_PLAN_CHANGE", TdtColors.CHANGE, TdtColors.CHANGE_BACKGROUND)
    val PLAN_DESTROY = key("TERRADUCKTEL_PLAN_DESTROY", TdtColors.DESTROY, TdtColors.DESTROY_BACKGROUND)
    val PLAN_REPLACE = key("TERRADUCKTEL_PLAN_REPLACE", TdtColors.REPLACE, TdtColors.REPLACE_BACKGROUND)

    val HEADER_OK = key("TERRADUCKTEL_HEADER_OK", TdtColors.OK, bold = true)
    val HEADER_RUN = key("TERRADUCKTEL_HEADER_RUN", TdtColors.RUN, bold = true)
    val HEADER_AWAITING = key("TERRADUCKTEL_HEADER_AWAITING", TdtColors.WARN, bold = true)
    val HEADER_FAILED = key("TERRADUCKTEL_HEADER_FAILED", TdtColors.ERR, bold = true)
    val HEADER_MUTED = key("TERRADUCKTEL_HEADER_MUTED", TdtColors.MUTED, bold = true)

    val ALL: List<TextAttributesKey> = listOf(
        PLAN_ADD, PLAN_CHANGE, PLAN_DESTROY, PLAN_REPLACE,
        HEADER_OK, HEADER_RUN, HEADER_AWAITING, HEADER_FAILED, HEADER_MUTED,
    )
}
