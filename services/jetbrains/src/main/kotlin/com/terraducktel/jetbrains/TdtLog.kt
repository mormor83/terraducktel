package com.terraducktel.jetbrains

import com.intellij.openapi.diagnostic.Logger

object TdtLog {
    val LOG: Logger = Logger.getInstance("#com.terraducktel")
    /** Request-level tracing; the settings `trace` flag gates it at the call site (Task 7). */
    fun trace(line: String) = LOG.info(line)
}
