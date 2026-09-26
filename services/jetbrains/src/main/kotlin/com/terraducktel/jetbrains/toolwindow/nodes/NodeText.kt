package com.terraducktel.jetbrains.toolwindow.nodes

import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.api.RunStep
import com.terraducktel.jetbrains.api.Workspace
import com.terraducktel.jetbrains.state.CloudGroup
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/** Secondary (grayed) row text, shared with the VS Code tree's `services/vscode/src/views/nodes.ts`. */
object NodeText {

    private val TIME = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")

    fun workspaceDescription(ws: Workspace, lastStatus: String?): String =
        listOfNotNull(lastStatus ?: "no runs", ws.repo_ref.ifBlank { null }, "drift".takeIf { ws.drift_status == "drifted" })
            .joinToString(" · ")

    fun runLabel(run: Run, workspaceName: String, underWorkspace: Boolean): String =
        if (underWorkspace) run.command else "$workspaceName · ${run.command}"

    fun runDescription(run: Run, zone: ZoneId = ZoneId.systemDefault()): String =
        listOfNotNull(run.status, run.branch?.ifBlank { null }, run.id.take(8), run.created_at?.let { localTime(it, zone) })
            .joinToString(" · ")

    fun stepDescription(step: RunStep): String {
        val s = step.duration_seconds ?: return ""
        return if (s % 1.0 == 0.0) "${s.toLong()}s" else "${s}s"
    }

    fun cloudDescription(group: CloudGroup): String = "${group.cloud.name} · ${group.count}"

    /** ISO timestamp → local `yyyy-MM-dd HH:mm`; anything unparseable is shown as-is. */
    private fun localTime(iso: String, zone: ZoneId): String {
        val instant = runCatching { OffsetDateTime.parse(iso).toInstant() }.getOrNull()
            ?: runCatching { Instant.parse(iso) }.getOrNull()
            ?: return iso
        return TIME.format(instant.atZone(zone))
    }
}
