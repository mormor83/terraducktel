# Application secrets live in SSM Parameter Store as SecureString (default
# aws/ssm KMS key). ECS task definitions reference them by ARN via the
# `secrets:` block, so plaintext never lands in the task def or the logs.
#
# The four core secrets are seeded from Terraform variables. The OIDC entries
# are seeded with placeholders and marked `ignore_changes = [value]` so an
# operator updates them once (via console/CLI) after the first apply without
# Terraform fighting the edit.

locals {
  ssm_prefix = "/${local.name_prefix}"
}

resource "aws_ssm_parameter" "credential_encryption_key" {
  name  = "${local.ssm_prefix}/credential-encryption-key"
  type  = "SecureString"
  value = var.credential_encryption_key
  lifecycle { prevent_destroy = true }
}

resource "aws_ssm_parameter" "jwt_secret" {
  name  = "${local.ssm_prefix}/jwt-secret"
  type  = "SecureString"
  value = var.jwt_secret
  lifecycle { prevent_destroy = true }
}

resource "aws_ssm_parameter" "state_token" {
  name  = "${local.ssm_prefix}/state-token"
  type  = "SecureString"
  value = var.state_token
  lifecycle { prevent_destroy = true }
}

resource "aws_ssm_parameter" "internal_token" {
  name        = "${local.ssm_prefix}/internal-token"
  description = "Guards /api/v1/internal/*. MUST differ from state-token; never given to executors."
  type        = "SecureString"
  value       = var.internal_token

  lifecycle {
    prevent_destroy = true
    # The executor/internal trust boundary collapses if these are equal.
    precondition {
      condition     = var.internal_token != var.state_token
      error_message = "internal_token must be different from state_token."
    }
  }
}

resource "aws_ssm_parameter" "db_password" {
  name  = "${local.ssm_prefix}/db-password"
  type  = "SecureString"
  value = var.db_password
  lifecycle { prevent_destroy = true }
}

resource "aws_ssm_parameter" "database_url" {
  name = "${local.ssm_prefix}/database-url"
  type = "SecureString"
  # urlencode the password so special chars (@ / : # ?) don't corrupt the DSN.
  # ?ssl=require pairs with the rds.force_ssl parameter group.
  value = format(
    "postgresql+asyncpg://%s:%s@%s/%s?ssl=require",
    aws_db_instance.this.username,
    urlencode(var.db_password),
    aws_db_instance.this.endpoint,
    aws_db_instance.this.db_name,
  )
  lifecycle { prevent_destroy = true }
}

# ─── OIDC / SSO (generic; works with any OIDC IdP) ──────────────────────────
# Placeholders — update after first apply, then force a new API deployment.

resource "aws_ssm_parameter" "auth_provider" {
  name  = "${local.ssm_prefix}/auth-provider"
  type  = "String"
  value = "local" # local | oidc | both — flip to "oidc"/"both" once OIDC is configured
  lifecycle { ignore_changes = [value] }
}

resource "aws_ssm_parameter" "oidc_issuer" {
  name  = "${local.ssm_prefix}/auth-oidc-issuer"
  type  = "String"
  value = "https://idp.example.com/" # trailing slash required
  lifecycle { ignore_changes = [value] }
}

resource "aws_ssm_parameter" "oidc_client_id" {
  name  = "${local.ssm_prefix}/auth-oidc-client-id"
  type  = "SecureString"
  value = "REPLACE-AFTER-APPLY"
  lifecycle { ignore_changes = [value] }
}

resource "aws_ssm_parameter" "oidc_client_secret" {
  name  = "${local.ssm_prefix}/auth-oidc-client-secret"
  type  = "SecureString"
  value = "REPLACE-AFTER-APPLY"
  lifecycle { ignore_changes = [value] }
}

resource "aws_ssm_parameter" "oidc_redirect_uri" {
  name  = "${local.ssm_prefix}/auth-oidc-redirect-uri"
  type  = "String"
  value = "https://${var.domain_name}/api/v1/auth/oidc/callback"
}

resource "aws_ssm_parameter" "oidc_role_mapping" {
  name = "${local.ssm_prefix}/auth-oidc-role-mapping"
  type = "String"
  value = jsonencode({
    "tdt-admins"    = "admin"
    "tdt-operators" = "operator"
  })
  lifecycle { ignore_changes = [value] }
}
