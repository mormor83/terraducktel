package com.terraducktel.jetbrains.toolwindow

import com.intellij.openapi.project.DumbAware
import com.intellij.openapi.project.Project
import com.intellij.openapi.wm.ToolWindow
import com.intellij.openapi.wm.ToolWindowFactory
import com.intellij.openapi.wm.ex.ToolWindowEx

/** The bottom "Terraducktel Run" tool window. It starts empty: [com.terraducktel.jetbrains.output.
 *  RunConsoles] adds one closeable console tab per watched run and activates the window. */
class TdtRunToolWindowFactory : ToolWindowFactory, DumbAware {
    override fun createToolWindowContent(project: Project, toolWindow: ToolWindow) {
        (toolWindow as? ToolWindowEx)?.emptyText?.text = "Watch, plan or apply a run to see its output here"
    }
}
