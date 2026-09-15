package com.terraducktel.jetbrains.session

import com.intellij.util.messages.Topic

/** Fired on the application message bus whenever [TdtSession] state the rest of the plugin needs
 *  to react to changes: profile, sign-in state, business unit, or `canWrite()`. */
interface TdtSessionListener {
    fun sessionChanged()

    companion object {
        val TOPIC: Topic<TdtSessionListener> = Topic.create("Terraducktel session", TdtSessionListener::class.java)
    }
}
