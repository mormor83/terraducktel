package com.terraducktel.jetbrains.actions

import org.junit.Assert.assertEquals
import org.junit.Test

/** Balloon bodies are rendered as HTML by the platform, and [ActionUtil.notify] callers interpolate
 *  server-controlled text (API error messages, workspace names, run commands) into them. */
class ActionUtilBodyTest {

    @Test fun `server text is escaped so it cannot inject markup into the balloon`() {
        assertEquals(
            "Terraducktel: &lt;a href=&quot;https://evil.example&quot;&gt;click&lt;/a&gt; &amp; more",
            ActionUtil.body("Terraducktel: <a href=\"https://evil.example\">click</a> & more"),
        )
    }

    @Test fun `plain text passes through unchanged`() {
        assertEquals("TDT: approved worker-pool apply.", ActionUtil.body("TDT: approved worker-pool apply."))
    }
}
