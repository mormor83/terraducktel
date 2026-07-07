variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix for all resource names."
  type        = string
  default     = "terraducktel"
}

variable "environment" {
  description = "Environment label (used in tags + resource names)."
  type        = string
  default     = "prod"
}

# ─── Networking ──────────────────────────────────────────────────────────────

variable "vpc_cidr" {
  description = "CIDR for the VPC this stack creates."
  type        = string
  default     = "10.20.0.0/16"
}

variable "az_count" {
  description = "Number of AZs to spread public/private subnets across (2 = HA)."
  type        = number
  default     = 2
}

variable "internal_alb" {
  description = "If true the ALB is internal (private, reachable only from the VPC/VPN); if false it is internet-facing. Internal is recommended."
  type        = bool
  default     = true
}

variable "trusted_ingress_cidrs" {
  description = "Extra CIDRs allowed to reach the ALB on 80/443 (e.g. your corporate VPN range). The VPC CIDR is always allowed."
  type        = list(string)
  default     = []
}

# ─── DNS / TLS ───────────────────────────────────────────────────────────────

variable "domain_name" {
  description = "Hostname the UI is served at, e.g. terraducktel.example.com. Must be covered by acm_certificate_arn."
  type        = string
  default     = "terraducktel.example.com"
}

variable "acm_certificate_arn" {
  description = "ARN of an ACM certificate covering domain_name (in this region). Required for the HTTPS listener."
  type        = string
}

variable "route53_zone_id" {
  description = "Optional Route 53 hosted-zone ID. If set, an ALIAS record domain_name -> ALB is created. Leave empty to manage DNS yourself."
  type        = string
  default     = ""
}

# ─── Images ──────────────────────────────────────────────────────────────────

variable "image_tag" {
  description = "Container image tag to deploy for every TDT service. Push images to the ECR repos this stack creates, then set this and apply."
  type        = string
  default     = "latest"
}

# ─── Application secrets (provide via TF_VAR_* env or a tfvars NOT committed) ──

variable "db_password" {
  description = "Initial RDS master password. Rotate after first apply."
  type        = string
  sensitive   = true
}

variable "credential_encryption_key" {
  description = "Key used to encrypt secrets in the TDT `config` table. Generate: python -c 'import secrets; print(secrets.token_urlsafe(32))'."
  type        = string
  sensitive   = true
}

variable "jwt_secret" {
  description = "HS256 signing key for TDT-issued JWTs. 32+ random bytes."
  type        = string
  sensitive   = true
}

variable "state_token" {
  description = "Secret for the Terraform HTTP state backend. Handed to executor tasks; keep it DIFFERENT from internal_token."
  type        = string
  sensitive   = true
}

variable "internal_token" {
  description = "Secret guarding the cross-tenant /api/v1/internal/* API (drift/liveness only). MUST differ from state_token and is NEVER given to executors."
  type        = string
  sensitive   = true
}

# ─── Fargate sizing ──────────────────────────────────────────────────────────

variable "api_cpu" {
  type    = number
  default = 512
}
variable "api_memory" {
  type    = number
  default = 1024
}
variable "ui_cpu" {
  type    = number
  default = 256
}
variable "ui_memory" {
  type    = number
  default = 512
}
variable "worker_cpu" {
  type    = number
  default = 256
}
variable "worker_memory" {
  type    = number
  default = 512
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.small"
}

variable "log_retention_days" {
  type    = number
  default = 30
}
