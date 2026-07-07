# Terraducktel on AWS ECS (Fargate) — reference deployment

A self-contained, **example** Terraform stack that runs Terraducktel on AWS ECS
Fargate. It creates its own VPC, so it has no dependency on existing
infrastructure or private modules — adapt it to your environment before using
it for real.

> ⚠️ **Not apply-tested.** This is a reviewed reference, not a turnkey module.
> Read it, adjust it to your org's networking/security standards, and run
> `terraform plan` carefully before applying. It provisions billable resources
> (NAT gateway, RDS, ALB, Fargate).

## What it creates

| Layer | Resources |
|---|---|
| Network | Dedicated VPC, public + private subnets across `az_count` AZs, IGW, single NAT gateway |
| Load balancer | ALB (internal by default) + HTTPS listener (your ACM cert), HTTP→HTTPS redirect; `/api/*`,`/health`,`/metrics` → api, everything else → ui |
| Compute | ECS Fargate cluster; services for `api`, `ui`, `drift-detector`, `liveness-detector`; an `executor` **task definition** the API launches on demand via the ECS RunTask API |
| Database | RDS Postgres 16 (encrypted, 7-day backups, deletion protection) |
| Registry | ECR repo per image (`api`, `ui`, `drift-detector`, `liveness-detector`, `executor`) |
| Secrets | SSM Parameter Store (SecureString) for the DB URL, encryption key, JWT secret, **state token + a separate internal token**, and OIDC config |
| Observability | CloudWatch log group per service |
| IAM | Task execution role (ECR pull, SSM read) + task role (S3 state bucket, `ecs:RunTask`/`PassRole` for executors) |

## Two service tokens (important)

The app uses **two distinct secrets** with different trust levels — keep them
different:

- `state_token` — guards the Terraform HTTP state backend; handed to executor
  tasks so `terraform init/plan/apply` can reach its own workspace's state.
- `internal_token` — guards the cross-tenant `/api/v1/internal/*` API (used
  only by the drift/liveness detectors) and is **never** given to executors.

## Prerequisites

1. AWS credentials with permission for VPC, RDS, ECS, ECR, IAM, ALB, ACM,
   Route 53, SSM, S3, CloudWatch, Logs.
2. An **ACM certificate** in this region covering `domain_name`.
3. DNS: either pass `route53_zone_id` (an ALIAS record is created for you) or
   point `domain_name` at the ALB yourself. The executor reaches the API at
   `https://<domain_name>`, so that name **must resolve inside the VPC**.
4. Images pushed to the ECR repos this stack creates (see below).

## Usage

```bash
cp terraform.tfvars.example terraform.tfvars   # then fill it in (or use TF_VAR_*)
terraform init
terraform apply -target=aws_ecr_repository.this   # create ECR repos first

# Build + push each image to its repo (api, ui, drift-detector,
# liveness-detector, executor) under the same tag, then:
terraform apply
```

After apply, seed the first admin user through the running API (or your
IdP/OIDC group mapping). Rotate `db_password` and the SSM secrets after the
first apply.

## Notes / trade-offs to review before production

- **Single NAT gateway** (cost). Use one-per-AZ for HA.
- **`db.t4g.small`, single-AZ** RDS. Size up + enable Multi-AZ for prod.
- The **executor** runs with its own deliberately-minimal task role (it executes
  user-supplied Terraform), isolated from the app role's tfstate/RunTask/PassRole
  access. The api/ui/detectors share one app role for brevity — split per-service
  for stricter least privilege.
- OIDC config is delivered via SSM `secrets:` (so `aws ssm put-parameter
  --overwrite` + a forced new deployment picks it up); placeholders use
  `ignore_changes = [value]` so your edits stick across `terraform apply`.
- **TLS to RDS is enforced** (`rds.force_ssl` + `?ssl=require`); `/metrics` is
  not routed through the ALB (scrape it in-VPC).
- Internet-facing mode (`internal_alb = false`) additionally admits the NAT EIP
  so in-VPC callers still work — but set `trusted_ingress_cidrs` explicitly.
- Remote state for *this* stack is left as local; wire an S3 backend in
  `versions.tf` before real use.
