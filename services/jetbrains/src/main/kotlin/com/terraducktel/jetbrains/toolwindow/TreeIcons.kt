package com.terraducktel.jetbrains.toolwindow

import com.intellij.openapi.util.IconLoader
import com.intellij.ui.AnimatedIcon
import javax.swing.Icon

/** Brand status / cloud icons for the Workspaces and Runs trees — the JetBrains side of
 *  `services/vscode/src/views/brand.ts`. Static icons are the 16px SVGs under `/icons/status` and
 *  `/icons/cloud` (each with a `_dark` sibling, picked by [IconLoader] per theme); in-flight rows
 *  get the platform's animated spinner. */
object TreeIcons {

    private val IN_FLIGHT = setOf("running", "planning", "applying")

    private val ASSET = mapOf(
        "applied" to "applied", "success" to "applied",
        "planned" to "planned",
        "awaiting_approval" to "awaiting",
        "failed" to "failed",
        "cancelled" to "cancelled", "skipped" to "cancelled",
        "pending" to "pending",
    )

    val TOOL_WINDOW: Icon = IconLoader.getIcon("/icons/tdt.svg", TreeIcons::class.java)

    /** Resource path of a settled status's icon (`none` covers "no runs" and unknown statuses);
     *  null while the status is still in flight. */
    fun statusIconPath(status: String?): String? {
        if (status in IN_FLIGHT) return null
        return "/icons/status/${ASSET[status] ?: "none"}.svg"
    }

    /** A workspace's last-run icon (null → no runs), or a run row's own icon. */
    fun runStatusIcon(status: String?): Icon = statusIconPath(status)?.let(::load) ?: AnimatedIcon.Default.INSTANCE

    fun stepStatusIcon(status: String?): Icon = runStatusIcon(status)

    fun cloudIconPath(cloud: String): String =
        "/icons/cloud/${cloud.lowercase().takeIf { it in setOf("aws", "azure", "gcp") } ?: "other"}.svg"

    fun cloudIcon(cloud: String): Icon = load(cloudIconPath(cloud))

    private fun load(path: String): Icon = IconLoader.getIcon(path, TreeIcons::class.java)
}
