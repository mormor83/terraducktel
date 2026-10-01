package com.terraducktel.jetbrains.ui

import com.intellij.ui.JBColor
import java.awt.Color

/**
 * Every Terraducktel brand colour, as a theme-aware [JBColor] (light, dark). Values come from the
 * design handoff (`docs/design_handoff_ide_plugins/README.md` → Design tokens). Nothing else in
 * the plugin may hard-code a colour; editor/console styling goes through [TdtTextAttributes], whose
 * defaults are built from these.
 */
object TdtColors {

    private fun rgb(hex: Int) = Color(hex)
    private fun rgba(r: Int, g: Int, b: Int, alpha: Double) = Color(r, g, b, Math.round(alpha * 255).toInt())

    /** name → (light, dark). `internal` so the token test can check raw values. */
    internal val PALETTE: Map<String, Pair<Color, Color>> = linkedMapOf(
        // Plan diff foregrounds and line backgrounds.
        "add" to (rgb(0x2f9b56) to rgb(0x7ed078)),
        "change" to (rgb(0xc98a14) to rgb(0xe0a93b)),
        "destroy" to (rgb(0xc4452f) to rgb(0xe05c45)),
        "replace" to (rgb(0xc98a14) to rgb(0xe0a93b)),
        "addBackground" to (rgb(0xe6f5ec) to rgba(126, 208, 120, .12)),
        "changeBackground" to (rgb(0xfbf2dc) to rgba(224, 169, 59, .13)),
        "destroyBackground" to (rgb(0xfbe7e2) to rgba(224, 92, 69, .13)),
        "replaceBackground" to (rgb(0xfbf2dc) to rgba(224, 169, 59, .18)),
        // Status inks (tree icons are pre-coloured SVGs; these tint console headers).
        "ok" to (rgb(0x2f7c34) to rgb(0x7ed078)),
        "planned" to (rgb(0x2e727e) to rgb(0x4fb3c4)),
        "run" to (rgb(0x5c7a12) to rgb(0xb6ff4b)),
        "warn" to (rgb(0x96690f) to rgb(0xe0a93b)),
        "err" to (rgb(0xb3402c) to rgb(0xe05c45)),
        "muted" to (rgb(0x4f6157) to rgb(0x6e857a)),
        // Brand accent: lime on dark (fills/icons only, never text on light), teal on light.
        "accent" to (rgb(0x1f6f6c) to rgb(0xb6ff4b)),
        // Text on the section-header count pill (drawn on the platform focus/accent colour).
        "onAccent" to (rgb(0xffffff) to rgb(0xffffff)),
    )

    internal val byName: Map<String, JBColor> = PALETTE.mapValues { (_, c) -> JBColor(c.first, c.second) }

    val ADD: JBColor = byName.getValue("add")
    val CHANGE: JBColor = byName.getValue("change")
    val DESTROY: JBColor = byName.getValue("destroy")
    val REPLACE: JBColor = byName.getValue("replace")
    val ADD_BACKGROUND: JBColor = byName.getValue("addBackground")
    val CHANGE_BACKGROUND: JBColor = byName.getValue("changeBackground")
    val DESTROY_BACKGROUND: JBColor = byName.getValue("destroyBackground")
    val REPLACE_BACKGROUND: JBColor = byName.getValue("replaceBackground")
    val OK: JBColor = byName.getValue("ok")
    val PLANNED: JBColor = byName.getValue("planned")
    val RUN: JBColor = byName.getValue("run")
    val WARN: JBColor = byName.getValue("warn")
    val ERR: JBColor = byName.getValue("err")
    val MUTED: JBColor = byName.getValue("muted")
    val ACCENT: JBColor = byName.getValue("accent")
    val ON_ACCENT: JBColor = byName.getValue("onAccent")
}
