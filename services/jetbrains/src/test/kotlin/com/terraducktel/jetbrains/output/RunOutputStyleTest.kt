package com.terraducktel.jetbrains.output

import com.intellij.execution.ui.ConsoleViewContentType
import com.terraducktel.jetbrains.ui.TdtTextAttributes
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Test

/** Header tinting in the run console: port of the handoff's `run-output` tone rule. */
class RunOutputStyleTest {

    @Test fun `step and run headers are tinted by status`() {
        val expected = mapOf(
            "── Plan [success]" to TdtTextAttributes.HEADER_OK,
            "── run applied" to TdtTextAttributes.HEADER_OK,
            "── run planned" to TdtTextAttributes.HEADER_OK,
            "── Plan [failed]" to TdtTextAttributes.HEADER_FAILED,
            "── run cancelled" to TdtTextAttributes.HEADER_FAILED,
            "── run awaiting_approval" to TdtTextAttributes.HEADER_AWAITING,
            "── Plan [running]" to TdtTextAttributes.HEADER_RUN,
            "── Init [pending]" to TdtTextAttributes.HEADER_RUN,
            "── Checkov [skipped]" to TdtTextAttributes.HEADER_MUTED,
        )
        for ((line, key) in expected) assertEquals(line, key, RunOutputStyle.headerKey(line))
    }

    @Test fun `each header key has its own registered console content type`() {
        val type = RunOutputStyle.contentTypeFor("── Plan [success]")
        assertSame(RunOutputStyle.typeFor(TdtTextAttributes.HEADER_OK), type)
        assertEquals(TdtTextAttributes.HEADER_OK, RunOutputStyle.typeFor(TdtTextAttributes.HEADER_OK).attributesKey)
    }

    @Test fun `errors and plain output keep the stock content types`() {
        assertSame(ConsoleViewContentType.ERROR_OUTPUT, RunOutputStyle.contentTypeFor("✕ boom"))
        assertSame(ConsoleViewContentType.NORMAL_OUTPUT, RunOutputStyle.contentTypeFor("aws_s3_bucket.b: Creating..."))
        assertSame(ConsoleViewContentType.NORMAL_OUTPUT, RunOutputStyle.contentTypeFor("── not a header"))
    }
}
