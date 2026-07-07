resource "aws_ecs_cluster" "this" {
  name = local.name_prefix
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
  tags = { Name = local.name_prefix }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE"]
  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

locals {
  # SecureString params exposed to containers as `secrets` (valueFrom = ARN).
  secret_core = {
    DATABASE_URL              = aws_ssm_parameter.database_url.arn
    CREDENTIAL_ENCRYPTION_KEY = aws_ssm_parameter.credential_encryption_key.arn
    JWT_SECRET_KEY            = aws_ssm_parameter.jwt_secret.arn
    TERRADUCKTEL_STATE_TOKEN  = aws_ssm_parameter.state_token.arn
  }
  # api + detectors additionally need the internal token; the api also gets OIDC.
  secret_internal = merge(local.secret_core, {
    TERRADUCKTEL_INTERNAL_TOKEN = aws_ssm_parameter.internal_token.arn
  })
  # The api also gets OIDC config. These are delivered via `secrets:`
  # (valueFrom SSM) — including the plain String params — so that editing an
  # SSM value + forcing a new deployment picks up the change WITHOUT a
  # `terraform apply` (an env value baked from `.value` would only update on
  # the next apply).
  secret_api = merge(local.secret_internal, {
    AUTH_OIDC_CLIENT_ID     = aws_ssm_parameter.oidc_client_id.arn
    AUTH_OIDC_CLIENT_SECRET = aws_ssm_parameter.oidc_client_secret.arn
    AUTH_OIDC_ISSUER        = aws_ssm_parameter.oidc_issuer.arn
    AUTH_OIDC_REDIRECT_URI  = aws_ssm_parameter.oidc_redirect_uri.arn
    AUTH_OIDC_ROLE_MAPPING  = aws_ssm_parameter.oidc_role_mapping.arn
    AUTH_MODE               = aws_ssm_parameter.auth_provider.arn
  })

  # Non-secret env shared by the app services.
  env_common = {
    AWS_DEFAULT_REGION = var.aws_region
    S3_USE_LOCALSTACK  = "false"
    S3_STATE_BUCKET    = aws_s3_bucket.tfstate.id
    PUBLIC_API_URL     = "https://${var.domain_name}"
  }

  # The API orchestrates executor tasks on Fargate via the ECS RunTask API.
  env_api = merge(local.env_common, {
    EXECUTOR_ENABLED  = "true"
    EXECUTOR_RUNTIME  = "ecs"
    EXECUTOR_CLUSTER  = aws_ecs_cluster.this.arn
    EXECUTOR_TASK_DEF = "${local.name_prefix}-executor"
    EXECUTOR_SUBNETS  = join(",", aws_subnet.private[*].id)
    EXECUTOR_SG       = aws_security_group.ecs.id
    EXECUTOR_IMAGE    = local.images["executor"]
  })
}

# Reusable factory would need a module; inline task defs keep this readable.

# ─── api ─────────────────────────────────────────────────────────────────────
resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name_prefix}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name         = "api"
    image        = local.images["api"]
    essential    = true
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
    environment  = [for k, v in local.env_api : { name = k, value = tostring(v) }]
    secrets      = [for k, v in local.secret_api : { name = k, valueFrom = v }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.svc["api"].name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "api"
      }
    }
  }])
}

# ─── ui (nginx) ──────────────────────────────────────────────────────────────
resource "aws_ecs_task_definition" "ui" {
  family                   = "${local.name_prefix}-ui"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.ui_cpu
  memory                   = var.ui_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name         = "ui"
    image        = local.images["ui"]
    essential    = true
    portMappings = [{ containerPort = 8080, protocol = "tcp" }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.svc["ui"].name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "ui"
      }
    }
  }])
}

# ─── drift-detector + liveness-detector (no load balancer) ──────────────────
resource "aws_ecs_task_definition" "detector" {
  for_each                 = toset(["drift-detector", "liveness-detector"])
  family                   = "${local.name_prefix}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name        = each.key
    image       = local.images[each.key]
    essential   = true
    environment = [for k, v in local.env_common : { name = k, value = tostring(v) }]
    secrets     = [for k, v in local.secret_internal : { name = k, valueFrom = v }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.svc[each.key].name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = each.key
      }
    }
  }])
}

# ─── executor (task def only — launched on demand by the API via RunTask) ───
resource "aws_ecs_task_definition" "executor" {
  family                   = "${local.name_prefix}-executor"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  execution_role_arn       = aws_iam_role.execution.arn
  # Minimal, isolated role — NOT the app task role (see iam.tf). User-supplied
  # Terraform runs here; it must not inherit tfstate/RunTask/PassRole access.
  task_role_arn = aws_iam_role.executor_task.arn

  container_definitions = jsonencode([{
    name      = "executor"
    image     = local.images["executor"]
    essential = true
    # Per-run env (RUN_ID, API_TOKEN, REPO_URL, GITHUB_TOKEN, …) is supplied by
    # the API as container overrides at RunTask time — NOT baked in here. The
    # global state/internal tokens are deliberately NOT provided to executors.
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.svc["executor"].name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "executor"
      }
    }
  }])
}

# ─── Services ────────────────────────────────────────────────────────────────
resource "aws_ecs_service" "api" {
  name            = "api"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener.https]
}

resource "aws_ecs_service" "ui" {
  name            = "ui"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.ui.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.ui.arn
    container_name   = "ui"
    container_port   = 8080
  }

  depends_on = [aws_lb_listener.https]
}

resource "aws_ecs_service" "detector" {
  for_each        = aws_ecs_task_definition.detector
  name            = each.key
  cluster         = aws_ecs_cluster.this.id
  task_definition = each.value.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = false
  }
}
