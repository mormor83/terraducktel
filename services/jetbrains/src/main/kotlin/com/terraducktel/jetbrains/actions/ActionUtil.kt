package com.terraducktel.jetbrains.actions

import com.intellij.notification.NotificationAction
import com.intellij.notification.NotificationGroupManager
import com.intellij.notification.NotificationType
import com.intellij.openapi.progress.ProgressIndicator
import com.intellij.openapi.progress.ProgressManager
import com.intellij.openapi.progress.Task
import com.intellij.openapi.project.Project
import com.terraducktel.jetbrains.api.ApiError
import java.io.IOException

/** Shared helpers for actions (and the session/sign-in flow) that need to run a blocking TDT call
 *  off the EDT and turn its failure into a balloon, or just show a balloon on its own. */
object ActionUtil {
    private const val GROUP_ID = "Terraducktel"

    /** Runs [block] as a (by default non-cancellable) background task titled [title]. An
     *  [ApiError], [IOException], [IllegalStateException] or [IllegalArgumentException] thrown
     *  from it is turned into an error balloon ("Terraducktel: <message>") instead of propagating —
     *  callers never need their own try/catch for these. */
    fun runBackground(project: Project?, title: String, cancellable: Boolean = false, block: (ProgressIndicator) -> Unit) {
        ProgressManager.getInstance().run(object : Task.Backgroundable(project, title, cancellable) {
            override fun run(indicator: ProgressIndicator) {
                try {
                    block(indicator)
                } catch (e: ApiError) {
                    err(project, e.message)
                } catch (e: IOException) {
                    err(project, e.message)
                } catch (e: IllegalStateException) {
                    err(project, e.message)
                } catch (e: IllegalArgumentException) {
                    err(project, e.message)
                }
            }
        })
    }

    private fun err(project: Project?, message: String?) {
        notify(project, "Terraducktel: ${message ?: "an unexpected error occurred"}", NotificationType.ERROR)
    }

    /** Shows a balloon in the "Terraducktel" notification group, optionally with one or more
     *  actions (label -> callback). */
    fun notify(project: Project?, text: String, type: NotificationType = NotificationType.INFORMATION, vararg actions: Pair<String, () -> Unit>) {
        val notification = NotificationGroupManager.getInstance().getNotificationGroup(GROUP_ID).createNotification(text, type)
        for ((label, action) in actions) {
            notification.addAction(NotificationAction.createSimpleExpiring(label) { action() })
        }
        notification.notify(project)
    }
}
