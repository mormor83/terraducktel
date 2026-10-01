package com.terraducktel.jetbrains.ui

import com.intellij.openapi.editor.colors.TextAttributesKey
import com.intellij.openapi.fileTypes.PlainSyntaxHighlighter
import com.intellij.openapi.fileTypes.SyntaxHighlighter
import com.intellij.openapi.options.colors.AttributesDescriptor
import com.intellij.openapi.options.colors.ColorDescriptor
import com.intellij.openapi.options.colors.ColorSettingsPage
import com.terraducktel.jetbrains.toolwindow.TreeIcons
import javax.swing.Icon

/** Settings → Editor → Color Scheme → Terraducktel: lets users override [TdtTextAttributes]. */
class TdtColorSettingsPage : ColorSettingsPage {

    private val descriptors = arrayOf(
        AttributesDescriptor("Plan//Added line", TdtTextAttributes.PLAN_ADD),
        AttributesDescriptor("Plan//Changed line", TdtTextAttributes.PLAN_CHANGE),
        AttributesDescriptor("Plan//Destroyed line", TdtTextAttributes.PLAN_DESTROY),
        AttributesDescriptor("Plan//Replaced line", TdtTextAttributes.PLAN_REPLACE),
        AttributesDescriptor("Run console//Step succeeded", TdtTextAttributes.HEADER_OK),
        AttributesDescriptor("Run console//Step running", TdtTextAttributes.HEADER_RUN),
        AttributesDescriptor("Run console//Awaiting approval", TdtTextAttributes.HEADER_AWAITING),
        AttributesDescriptor("Run console//Step failed", TdtTextAttributes.HEADER_FAILED),
        AttributesDescriptor("Run console//Step skipped", TdtTextAttributes.HEADER_MUTED),
    )

    private val tags = mapOf(
        "add" to TdtTextAttributes.PLAN_ADD,
        "change" to TdtTextAttributes.PLAN_CHANGE,
        "destroy" to TdtTextAttributes.PLAN_DESTROY,
        "replace" to TdtTextAttributes.PLAN_REPLACE,
        "ok" to TdtTextAttributes.HEADER_OK,
        "run" to TdtTextAttributes.HEADER_RUN,
        "awaiting" to TdtTextAttributes.HEADER_AWAITING,
        "failed" to TdtTextAttributes.HEADER_FAILED,
        "muted" to TdtTextAttributes.HEADER_MUTED,
    )

    override fun getDisplayName(): String = "Terraducktel"
    override fun getIcon(): Icon = TreeIcons.TOOL_WINDOW
    override fun getHighlighter(): SyntaxHighlighter = PlainSyntaxHighlighter()
    override fun getAttributeDescriptors(): Array<AttributesDescriptor> = descriptors
    override fun getColorDescriptors(): Array<ColorDescriptor> = ColorDescriptor.EMPTY_ARRAY
    override fun getAdditionalHighlightingTagToDescriptorMap(): Map<String, TextAttributesKey> = tags

    override fun getDemoText(): String = """
        |Terraform will perform the following actions:
        |<add>  + resource "aws_s3_bucket" "logs" {</add>
        |<change>  ~ resource "aws_iam_role" "worker" {</change>
        |<destroy>  - resource "aws_instance" "old" {</destroy>
        |<replace>-/+ resource "aws_launch_template" "pool" {</replace>
        |
        |<ok>── Init [success]</ok>
        |<run>── Plan [running]</run>
        |<muted>── Checkov [skipped]</muted>
        |<failed>── Apply [failed]</failed>
        |<awaiting>── run awaiting_approval</awaiting>
        |""".trimMargin()
}
