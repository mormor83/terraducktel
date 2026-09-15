package com.terraducktel.jetbrains.toolwindow.nodes

import com.intellij.ide.projectView.PresentationData
import com.intellij.openapi.project.Project
import com.intellij.ui.SimpleTextAttributes
import com.terraducktel.jetbrains.api.RunStep
import com.terraducktel.jetbrains.toolwindow.TreeIcons

/** A leaf of a [RunNode]: one plan/apply step. Never has children (steps don't nest). */
class StepNode(
    project: Project,
    parent: RunNode,
    val step: RunStep,
) : TdtNode(project, parent) {

    override val id: String = "step:${parent.run.id}:${step.position}"

    override fun buildChildren(): List<TdtNode> = emptyList()

    override fun update(presentation: PresentationData) {
        presentation.addText(step.name, SimpleTextAttributes.REGULAR_ATTRIBUTES)
        presentation.addText(" ${step.status}", SimpleTextAttributes.GRAYED_ATTRIBUTES)
        presentation.setIcon(TreeIcons.stepStatusIcon(step.status))
    }
}
