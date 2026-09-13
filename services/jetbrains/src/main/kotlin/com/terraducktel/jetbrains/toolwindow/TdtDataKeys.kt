package com.terraducktel.jetbrains.toolwindow

import com.intellij.openapi.actionSystem.DataKey
import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.api.Workspace

/** Context-menu data keys populated by [TreePanel.uiDataSnapshot] from the tree's current
 *  selection — consumed by Tasks 10/11's `Terraducktel.WorkspaceMenu` / `Terraducktel.RunMenu`
 *  actions. */
object TdtDataKeys {
    val WORKSPACE: DataKey<Workspace> = DataKey.create("terraducktel.workspace")
    val RUN: DataKey<Run> = DataKey.create("terraducktel.run")
}
