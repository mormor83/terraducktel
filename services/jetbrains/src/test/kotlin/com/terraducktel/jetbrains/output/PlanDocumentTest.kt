package com.terraducktel.jetbrains.output

import com.intellij.openapi.fileEditor.TextEditor
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.terraducktel.jetbrains.ui.TdtTextAttributes
import org.junit.Assert.*
import org.junit.Test

/**
 * Plain-JUnit port of `services/vscode/test/unit/planDocument.test.ts`'s `planLineKinds` cases
 * onto [PlanDocument.classifyLine] (pure — no platform machinery needed), plus [PlanDocument
 * .fileNameFor]. The platform-dependent half ([PlanDocument.openText] building a real read-only
 * editor with line highlighters) is [PlanDocumentPlatformTest] below, since it needs a
 * [BasePlatformTestCase] project/editor and the two bases can't be mixed in one class.
 */
class PlanDocumentTest {

    @Test fun `plus-space is ADD`() {
        assertEquals(PlanLineKind.ADD, PlanDocument.classifyLine("+ resource \"aws_s3_bucket\" \"b\" {"))
    }

    @Test fun `indented plus-space is still ADD`() {
        assertEquals(PlanLineKind.ADD, PlanDocument.classifyLine("      + bucket = \"x\""))
    }

    @Test fun `minus-space is DELETE`() {
        assertEquals(PlanLineKind.DELETE, PlanDocument.classifyLine("  - resource \"x\" \"y\" {"))
    }

    @Test fun `tilde-space is CHANGE`() {
        assertEquals(PlanLineKind.CHANGE, PlanDocument.classifyLine("  ~ update in-place"))
    }

    @Test fun `dash-slash-plus is REPLACE`() {
        assertEquals(PlanLineKind.REPLACE, PlanDocument.classifyLine("-/+ resource \"a\" \"b\" (replace)"))
    }

    @Test fun `plus-slash-minus is also REPLACE`() {
        assertEquals(PlanLineKind.REPLACE, PlanDocument.classifyLine("+/- resource \"a\" \"b\" (replace)"))
    }

    @Test fun `dash-slash-plus with no trailing space is still REPLACE`() {
        // planDocument.ts uses a bare startsWith("-/+") with no space requirement — unlike the
        // single-character +/-/~ markers, which do require one (see the bare-marker tests below).
        assertEquals(PlanLineKind.REPLACE, PlanDocument.classifyLine("-/+resource \"a\" \"b\" (replace)"))
    }

    @Test fun `plus-slash-minus with no trailing space is still REPLACE`() {
        assertEquals(PlanLineKind.REPLACE, PlanDocument.classifyLine("+/-resource \"a\" \"b\" (replace)"))
    }

    @Test fun `comment lines are NONE`() {
        assertEquals(PlanLineKind.NONE, PlanDocument.classifyLine("  # aws_s3_bucket.b will be created"))
    }

    @Test fun `plain narrative lines are NONE`() {
        assertEquals(PlanLineKind.NONE, PlanDocument.classifyLine("Terraform will perform the following actions:"))
        assertEquals(PlanLineKind.NONE, PlanDocument.classifyLine("Plan: 1 to add, 1 to change, 1 to destroy."))
    }

    @Test fun `a bare plus with no following space is NONE`() {
        assertEquals(PlanLineKind.NONE, PlanDocument.classifyLine("+"))
    }

    @Test fun `a bare minus with no following space is NONE`() {
        assertEquals(PlanLineKind.NONE, PlanDocument.classifyLine("-"))
    }

    @Test fun `blank line is NONE`() {
        assertEquals(PlanLineKind.NONE, PlanDocument.classifyLine(""))
    }

    @Test fun `fileNameFor takes the first 8 chars of the run id`() {
        assertEquals("vpc-01234567.tfplan.txt", PlanDocument.fileNameFor("vpc", "0123456789abcdef"))
    }

    @Test fun `fileNameFor with a short run id just uses all of it`() {
        assertEquals("vpc-abc.tfplan.txt", PlanDocument.fileNameFor("vpc", "abc"))
    }
}

/**
 * Platform half: [PlanDocument.openText] opens a real, read-only editor over the given text and
 * decorates it with one line highlighter per `+`/`-`/`~` line.
 */
class PlanDocumentPlatformTest : BasePlatformTestCase() {

    fun `test openText opens a read-only editor with one brand highlighter per decorated line`() {
        val editor = PlanDocument.openText(project, "x.tfplan.txt", "+ a\n- b\n~ c\n-/+ d\n")

        assertNotNull("expected a FileEditor to be returned", editor)
        assertTrue("expected a TextEditor", editor is TextEditor)
        val textEditor = editor as TextEditor
        assertFalse("plan document must be read-only", textEditor.editor.document.isWritable)

        val document = textEditor.editor.document
        val highlighters = textEditor.editor.markupModel.allHighlighters
        assertEquals(4, highlighters.size)

        // Per-line, not just "the right set of keys somewhere" — swapping PLAN_ADD/PLAN_DESTROY in
        // PlanDocument.keyFor would still pass a set-only assertion.
        val byLine = highlighters.associateBy { document.getLineNumber(it.startOffset) }
        assertEquals(TdtTextAttributes.PLAN_ADD, byLine[0]?.getTextAttributesKey()) // "+ a"
        assertEquals(TdtTextAttributes.PLAN_DESTROY, byLine[1]?.getTextAttributesKey()) // "- b"
        assertEquals(TdtTextAttributes.PLAN_CHANGE, byLine[2]?.getTextAttributesKey()) // "~ c"
        assertEquals(TdtTextAttributes.PLAN_REPLACE, byLine[3]?.getTextAttributesKey()) // "-/+ d"
        // The replace colour's 2px left bar is a gutter line-marker, on replace lines only.
        assertNotNull(byLine[3]?.lineMarkerRenderer)
        assertNull(byLine[0]?.lineMarkerRenderer)
    }
}
