package com.terraducktel.jetbrains.ui

import com.intellij.testFramework.fixtures.BasePlatformTestCase
import java.awt.Font

class TdtColorSettingsPageTest : BasePlatformTestCase() {

    fun `test every brand key is listed on the Terraducktel colour page and tagged in its demo text`() {
        val page = TdtColorSettingsPage()
        assertEquals("Terraducktel", page.displayName)
        assertEquals(TdtTextAttributes.ALL.toSet(), page.attributeDescriptors.map { it.key }.toSet())
        val tags = page.additionalHighlightingTagToDescriptorMap
        assertEquals(TdtTextAttributes.ALL.toSet(), tags.values.toSet())
        for (tag in tags.keys) assertTrue(tag, page.demoText.contains("<$tag>"))
    }

    fun `test keys default to the TdtColors brand palette`() {
        val add = TdtTextAttributes.PLAN_ADD.defaultAttributes
        assertEquals(TdtColors.ADD, add.foregroundColor)
        assertEquals(TdtColors.ADD_BACKGROUND, add.backgroundColor)
        assertEquals(TdtColors.REPLACE_BACKGROUND, TdtTextAttributes.PLAN_REPLACE.defaultAttributes.backgroundColor)
        val ok = TdtTextAttributes.HEADER_OK.defaultAttributes
        assertEquals(TdtColors.OK, ok.foregroundColor)
        assertEquals(Font.BOLD, ok.fontType)
        assertEquals(TdtColors.ERR, TdtTextAttributes.HEADER_FAILED.defaultAttributes.foregroundColor)
    }
}
