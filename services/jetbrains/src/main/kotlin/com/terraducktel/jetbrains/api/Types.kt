package com.terraducktel.jetbrains.api

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement

val TdtJson = Json { ignoreUnknownKeys = true; explicitNulls = false; encodeDefaults = false }

@Serializable data class AuthConfig(val mode: String = "local", val oidc_enabled: Boolean = false, val oidc_issuer: String? = null, val cli_loopback: Boolean? = null)
@Serializable data class TokenPair(val access_token: String, val refresh_token: String, val token_type: String? = null)
@Serializable data class BusinessUnit(val id: String, val slug: String, val name: String)
@Serializable data class Workspace(
    val id: String, val business_unit_id: String = "", val name: String, val environment: String = "",
    val aws_account_id: String? = null, val region: String = "", val repo_url: String? = null, val tf_working_dir: String = "",
    val repo_ref: String = "", val kind: String = "terraform", val cluster_id: String? = null, val tags: Map<String, String> = emptyMap(),
    val drift_status: String = "unknown", val path_status: String = "ok", val azure_subscription_id: String? = null,
    val gcp_project_id: String? = null, val state_backend: String = "s3", val created_at: String? = null,
)
val TERMINAL_RUN_STATUSES: Set<String> = setOf("planned", "applied", "failed", "cancelled")
/** Statuses at which a plan-phase watcher may stop: the plan landed or is awaiting approval. */
val PLAN_LANDED_STATUSES: Set<String> = TERMINAL_RUN_STATUSES + "awaiting_approval"
val CANCELLABLE_RUN_STATUSES: Set<String> = setOf("pending", "running", "planning", "awaiting_approval")
@Serializable data class Run(
    val id: String, val workspace_id: String, val command: String, val status: String, val branch: String? = null,
    val triggered_by: String? = null, val policy_status: String? = null, val created_at: String? = null,
    val started_at: String? = null, val completed_at: String? = null,
)
@Serializable data class RunStep(
    val id: String = "", val run_id: String = "", val position: Int, val name: String, val status: String,
    val started_at: String? = null, val completed_at: String? = null, val duration_seconds: Double? = null,
    val output: String? = null, val summary_json: String? = null,
)
@Serializable data class GraphSummary(val add: Int = 0, val change: Int = 0, val destroy: Int = 0, val replace: Int = 0)
@Serializable data class RunGraph(val nodes: List<JsonElement> = emptyList(), val edges: List<JsonElement> = emptyList(), val summary: GraphSummary = GraphSummary())
@Serializable data class Branches(val source: String = "", val default_branch: String? = null, val branches: List<String> = emptyList())
@Serializable data class PlanOutput(val plan_output: String? = null)
@Serializable data class TriggerRunBody(val command: String, val branch: String? = null)
@Serializable data class LoginBody(val email: String, val password: String)
@Serializable data class RefreshBody(val refresh_token: String)
@Serializable data class RepoRefPatch(val repo_ref: String)
@Serializable data class RejectBody(val comment: String)
