data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    # Confused-deputy guard: only this account's ECS may assume these roles.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

# ─── Execution role: pull images, read SSM secrets, write logs ──────────────
resource "aws_iam_role" "execution" {
  name               = "${local.name_prefix}-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secrets" {
  statement {
    sid       = "ReadSsmParams"
    actions   = ["ssm:GetParameters", "ssm:GetParameter"]
    resources = ["arn:aws:ssm:${var.aws_region}:${local.account_id}:parameter${local.ssm_prefix}/*"]
  }
  # SecureString params here use the AWS-managed `aws/ssm` key, which needs no
  # explicit kms:Decrypt grant. If you switch to a customer-managed key, add a
  # kms:Decrypt statement scoped to that key's ARN (not its alias).
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "read-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

# ─── App task role: runtime permissions for api / ui / detectors ────────────
# The api launches executor tasks (EXECUTOR_RUNTIME=ecs) and reads/writes the
# tfstate bucket. ui/detectors share this role for brevity (split per-service
# for strict least privilege). The EXECUTOR does NOT use this role — see below.
resource "aws_iam_role" "task" {
  name               = "${local.name_prefix}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "task" {
  statement {
    sid       = "RunExecutorTasks"
    actions   = ["ecs:RunTask"]
    resources = ["arn:aws:ecs:${var.aws_region}:${local.account_id}:task-definition/${local.name_prefix}-executor:*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.this.arn]
    }
  }
  # StopTask/DescribeTasks authorize against TASK ARNs, not task-definition ARNs.
  statement {
    sid       = "PollExecutorTasks"
    actions   = ["ecs:StopTask", "ecs:DescribeTasks"]
    resources = ["arn:aws:ecs:${var.aws_region}:${local.account_id}:task/${local.name_prefix}/*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.this.arn]
    }
  }
  # Pass the EXECUTOR's roles (not this app role) to the launched task.
  statement {
    sid       = "PassExecutorRoles"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.execution.arn, aws_iam_role.executor_task.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
  statement {
    sid       = "DescribeForNetworking"
    actions   = ["ec2:DescribeSubnets", "ec2:DescribeSecurityGroups"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "app-runtime"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

# ─── Executor task role: DELIBERATELY minimal ───────────────────────────────
# The executor runs user-supplied Terraform (arbitrary providers / `external`
# data sources = arbitrary code). It must NOT carry the app role's tfstate-bucket
# access, RunTask, or PassRole — otherwise a malicious workspace could pull the
# task role via the metadata endpoint and reach every tenant's state. Per-run
# credentials arrive as RunTask container overrides (a run-scoped API token),
# not via this role. It stays empty (the execution role still pulls the image
# + reads only this run's needs).
resource "aws_iam_role" "executor_task" {
  name               = "${local.name_prefix}-executor-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}
