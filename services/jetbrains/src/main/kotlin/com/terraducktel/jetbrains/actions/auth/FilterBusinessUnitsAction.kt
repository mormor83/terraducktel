package com.terraducktel.jetbrains.actions.auth

import com.intellij.icons.AllIcons
import com.intellij.notification.NotificationType
import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.DialogWrapper
import com.intellij.openapi.ui.ValidationInfo
import com.intellij.ui.CheckBoxList
import com.intellij.ui.components.JBScrollPane
import com.intellij.util.ui.JBUI
import com.terraducktel.jetbrains.actions.ActionUtil
import com.terraducktel.jetbrains.api.BusinessUnit
import com.terraducktel.jetbrains.session.TdtSession
import com.terraducktel.jetbrains.state.BuFilter
import com.terraducktel.jetbrains.state.Store
import java.awt.Dimension
import javax.swing.JComponent

/**
 * "Filter Business Units…": ticks which of the user's business units the Workspaces and Runs
 * trees (and the approval notifications) show. Replaces the old single-BU switcher — every
 * accessible BU is a top-level row, and this only hides some of them. The choice is remembered per
 * profile (see [Store.applyFilter]); selecting none is not allowed.
 */
class FilterBusinessUnitsAction : AnAction("Filter Business Units…", "Choose which business units to show", AllIcons.General.Filter) {
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun update(e: AnActionEvent) {
        e.presentation.isEnabled = TdtSession.getInstance().isSignedIn() && Store.getInstance().businessUnits.isNotEmpty()
    }

    override fun actionPerformed(e: AnActionEvent) {
        show(e.project)
    }

    companion object {
        /** Opens the checkbox dialog (pre-ticked with the BUs that are visible now) and applies the
         *  choice on OK. Cancelling keeps the previous filter. Must be called on the EDT. */
        fun show(project: Project?) {
            val store = Store.getInstance()
            val all = store.businessUnits.sortedWith(compareBy({ it.name.lowercase() }, { it.slug }))
            if (all.isEmpty()) {
                ActionUtil.notify(project, "Terraducktel: no business units loaded yet.", NotificationType.WARNING)
                return
            }
            val dialog = BuFilterDialog(project, all, store.visibleBus().map { it.slug }.toSet())
            if (dialog.showAndGet()) store.applyFilter(dialog.selectedSlugs())
        }
    }
}

/** The checkbox list behind [FilterBusinessUnitsAction]; OK is refused while nothing is ticked. */
internal class BuFilterDialog(
    project: Project?,
    private val all: List<BusinessUnit>,
    visibleSlugs: Set<String>,
) : DialogWrapper(project) {

    private val list = CheckBoxList<BusinessUnit>().also { l ->
        for (bu in all) l.addItem(bu, "${bu.name}  (${bu.slug})", bu.slug in visibleSlugs)
        l.setCheckBoxListListener { _, _ -> initValidation() }
    }

    init {
        title = "Filter Business Units"
        init()
    }

    fun selectedSlugs(): Set<String> =
        all.filter { list.isItemSelected(it) }.map { it.slug }.toSet()

    override fun createCenterPanel(): JComponent =
        JBScrollPane(list).apply { preferredSize = Dimension(JBUI.scale(360), JBUI.scale(220)) }

    override fun doValidate(): ValidationInfo? =
        if (BuFilter.hiddenForSelection(all, selectedSlugs()) == null) ValidationInfo("Select at least one business unit.", list) else null
}
