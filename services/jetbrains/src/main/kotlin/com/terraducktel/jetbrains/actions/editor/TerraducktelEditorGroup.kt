package com.terraducktel.jetbrains.actions.editor

import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.actionSystem.DefaultActionGroup
import com.terraducktel.jetbrains.editor.EditorStatus

/**
 * The "Terraducktel" submenu `plugin.xml` adds to `EditorPopupMenu`. A plain `<group popup="true">`
 * compiles to a bare [DefaultActionGroup], which — on this platform version — shows up on EVERY
 * right-click regardless of whether either child action is visible: non-Terraform files, unmapped
 * `.tf` files, a signed-out or viewer session all got a greyed-out "Terraducktel" submenu.
 * `ActionGroup`/`DefaultActionGroup` expose no `hideIfNoVisibleChildren`-style override on this
 * platform's API (confirmed against the compiled SDK — it simply doesn't exist here to override),
 * so [update] hides the group directly instead: the same "is the active file mapped to a
 * workspace" check [PlanCurrentFileAction] and [RevealCurrentWorkspaceAction] already gate their
 * own visibility on.
 */
class TerraducktelEditorGroup : DefaultActionGroup() {
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun update(e: AnActionEvent) {
        super.update(e)
        val project = e.project
        val mapped = project != null && EditorStatus.getInstance(project).current != null
        e.presentation.isVisible = e.presentation.isVisible && mapped
    }
}
