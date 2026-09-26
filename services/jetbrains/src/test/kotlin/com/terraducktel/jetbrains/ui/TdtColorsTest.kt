package com.terraducktel.jetbrains.ui

import org.junit.Assert.assertEquals
import org.junit.Test
import java.awt.Color

/** [TdtColors.PALETTE] against the design handoff's tokens (docs/design_handoff_ide_plugins/README.md
 *  → Design tokens). Each entry is (light, dark). */
class TdtColorsTest {

    private fun rgb(hex: Int) = Color(hex)
    private fun rgba(r: Int, g: Int, b: Int, alpha: Double) = Color(r, g, b, Math.round(alpha * 255).toInt())

    private fun assertPair(name: String, light: Color, dark: Color) {
        val (l, d) = TdtColors.PALETTE.getValue(name)
        assertEquals("$name light", light, l)
        assertEquals("$name dark", dark, d)
        assertEquals("$name light alpha", light.alpha, l.alpha)
        assertEquals("$name dark alpha", dark.alpha, d.alpha)
    }

    @Test fun `diff foregrounds`() {
        assertPair("add", rgb(0x2f9b56), rgb(0x7ed078))
        assertPair("change", rgb(0xc98a14), rgb(0xe0a93b))
        assertPair("destroy", rgb(0xc4452f), rgb(0xe05c45))
        assertPair("replace", rgb(0xc98a14), rgb(0xe0a93b))
    }

    @Test fun `diff line backgrounds`() {
        assertPair("addBackground", rgb(0xe6f5ec), rgba(126, 208, 120, .12))
        assertPair("changeBackground", rgb(0xfbf2dc), rgba(224, 169, 59, .13))
        assertPair("destroyBackground", rgb(0xfbe7e2), rgba(224, 92, 69, .13))
        assertPair("replaceBackground", rgb(0xfbf2dc), rgba(224, 169, 59, .18))
    }

    @Test fun `status inks and accent`() {
        assertPair("ok", rgb(0x2f7c34), rgb(0x7ed078))
        assertPair("planned", rgb(0x2e727e), rgb(0x4fb3c4))
        assertPair("run", rgb(0x5c7a12), rgb(0xb6ff4b))
        assertPair("warn", rgb(0x96690f), rgb(0xe0a93b))
        assertPair("err", rgb(0xb3402c), rgb(0xe05c45))
        assertPair("muted", rgb(0x4f6157), rgb(0x6e857a))
        assertPair("accent", rgb(0x1f6f6c), rgb(0xb6ff4b))
    }

    @Test fun `every named constant is backed by the palette`() {
        assertEquals(TdtColors.PALETTE.keys, TdtColors.byName.keys)
    }
}
