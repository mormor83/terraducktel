package com.terraducktel.jetbrains.toolwindow

import com.intellij.icons.AllIcons
import com.intellij.ide.util.PropertiesComponent
import com.intellij.openapi.actionSystem.ActionManager
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.DefaultActionGroup
import com.intellij.openapi.project.Project
import com.intellij.ui.components.JBLabel
import com.intellij.util.ui.JBUI
import com.terraducktel.jetbrains.ui.TdtColors
import java.awt.BorderLayout
import java.awt.Cursor
import java.awt.Dimension
import java.awt.FlowLayout
import java.awt.Graphics
import java.awt.Graphics2D
import java.awt.RenderingHints
import java.awt.event.MouseAdapter
import java.awt.event.MouseEvent
import javax.swing.JComponent
import javax.swing.JPanel
import javax.swing.SwingUtilities

/**
 * One section of the stacked Terraducktel tool window: a 28px header (chevron, 12px bold title, an
 * optional count pill, and actions shown while the pointer is over it) above [body]. Clicking the
 * header toggles it; the collapsed state is remembered per project under [collapsedKey]
 * ([PropertiesComponent]). [onToggle] lets the owner give the free space to the other section.
 */
class CollapsibleSection(
    private val project: Project,
    val title: String,
    val body: JComponent,
    private val collapsedKey: String,
    headerActions: List<AnAction> = emptyList(),
    private val onToggle: () -> Unit = {},
) : JPanel(BorderLayout()) {

    private val chevron = JBLabel()
    private val pill = Pill()
    private val toolbar = ActionManager.getInstance()
        .createActionToolbar("TerraducktelSectionHeader", DefaultActionGroup(headerActions), true)
        .also { it.targetComponent = body }
    val header: JPanel = JPanel(BorderLayout())

    var isCollapsed: Boolean = PropertiesComponent.getInstance(project).getBoolean(collapsedKey)
        private set

    /** Count shown in the header pill; null hides it. */
    var badge: String? = null
        set(value) {
            field = value
            pill.text = value
            pill.isVisible = value != null
            header.revalidate()
            header.repaint()
        }

    init {
        val left = JPanel(FlowLayout(FlowLayout.LEFT, JBUI.scale(4), 0)).apply { isOpaque = false }
        left.add(chevron)
        left.add(JBLabel(title).apply { font = JBUI.Fonts.label(12f).asBold() })
        left.add(pill)
        pill.isVisible = false
        toolbar.component.isOpaque = false
        toolbar.component.border = JBUI.Borders.empty()
        toolbar.component.isVisible = false

        header.isOpaque = false
        header.border = JBUI.Borders.empty(0, 4)
        header.preferredSize = Dimension(0, JBUI.scale(HEADER_HEIGHT))
        header.minimumSize = Dimension(0, JBUI.scale(HEADER_HEIGHT))
        header.cursor = Cursor.getPredefinedCursor(Cursor.HAND_CURSOR)
        header.add(left, BorderLayout.CENTER)
        header.add(toolbar.component, BorderLayout.EAST)
        header.addMouseListener(object : MouseAdapter() {
            override fun mouseClicked(e: MouseEvent) { if (SwingUtilities.isLeftMouseButton(e)) setCollapsed(!isCollapsed) }
            override fun mouseEntered(e: MouseEvent) { toolbar.component.isVisible = true }
            override fun mouseExited(e: MouseEvent) {
                // Moving onto the toolbar's own buttons also "exits" the header — keep it shown then.
                val p = SwingUtilities.convertPoint(e.component, e.point, header)
                if (!header.contains(p)) toolbar.component.isVisible = false
            }
        })

        add(header, BorderLayout.NORTH)
        add(body, BorderLayout.CENTER)
        applyState()
    }

    fun setCollapsed(collapsed: Boolean) {
        if (collapsed == isCollapsed) return
        isCollapsed = collapsed
        PropertiesComponent.getInstance(project).setValue(collapsedKey, collapsed)
        applyState()
        onToggle()
    }

    private fun applyState() {
        chevron.icon = if (isCollapsed) AllIcons.General.ArrowRight else AllIcons.General.ArrowDown
        body.isVisible = !isCollapsed
        revalidate()
        repaint()
    }

    /** 16px-tall, fully rounded count pill in the focus/accent colour with white 10px text. */
    private class Pill : JComponent() {
        var text: String? = null

        override fun getPreferredSize(): Dimension {
            val fm = getFontMetrics(pillFont())
            val w = maxOf(JBUI.scale(16), fm.stringWidth(text.orEmpty()) + JBUI.scale(10))
            return Dimension(w, JBUI.scale(16))
        }

        override fun paintComponent(g: Graphics) {
            val label = text ?: return
            val g2 = g.create() as Graphics2D
            try {
                g2.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON)
                g2.setRenderingHint(RenderingHints.KEY_TEXT_ANTIALIASING, RenderingHints.VALUE_TEXT_ANTIALIAS_ON)
                val h = JBUI.scale(16)
                val y = (height - h) / 2
                g2.color = JBUI.CurrentTheme.Focus.focusColor()
                g2.fillRoundRect(0, y, width, h, h, h)
                g2.font = pillFont()
                g2.color = TdtColors.ON_ACCENT
                val fm = g2.fontMetrics
                g2.drawString(label, (width - fm.stringWidth(label)) / 2, y + (h - fm.height) / 2 + fm.ascent)
            } finally {
                g2.dispose()
            }
        }

        private fun pillFont() = JBUI.Fonts.label(10f).asBold()
    }

    companion object {
        const val HEADER_HEIGHT = 28
    }
}
