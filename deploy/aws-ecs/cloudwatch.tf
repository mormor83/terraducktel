resource "aws_cloudwatch_log_group" "svc" {
  for_each          = toset(["api", "ui", "drift-detector", "liveness-detector", "executor"])
  name              = "/ecs/${local.name_prefix}/${each.key}"
  retention_in_days = var.log_retention_days
  tags              = { Name = "${local.name_prefix}-${each.key}-logs" }
}
