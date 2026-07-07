resource "aws_security_group" "alb" {
  name        = "${local.name_prefix}-alb"
  description = "Terraducktel ALB"
  vpc_id      = aws_vpc.this.id
  tags        = { Name = "${local.name_prefix}-alb-sg" }
}

# 80/443 from the VPC itself plus any operator-supplied trusted CIDRs
# (e.g. a corporate VPN range). Nothing here is 0.0.0.0/0 by default.
resource "aws_security_group_rule" "alb_ingress" {
  for_each = {
    # distinct() so an overlapping trusted CIDR (e.g. the VPC CIDR) doesn't
    # produce a duplicate for_each key. When the ALB is internet-facing, the
    # NAT EIP is included so in-VPC callers (executor/detectors) that resolve
    # the public ALB and egress via NAT are still admitted.
    for pair in setproduct([80, 443], distinct(concat(
      [var.vpc_cidr],
      var.trusted_ingress_cidrs,
      var.internal_alb ? [] : ["${aws_eip.nat.public_ip}/32"],
    ))) :
    "${pair[0]}-${pair[1]}" => { port = pair[0], cidr = pair[1] }
  }
  type              = "ingress"
  security_group_id = aws_security_group.alb.id
  protocol          = "tcp"
  from_port         = each.value.port
  to_port           = each.value.port
  cidr_blocks       = [each.value.cidr]
  description       = "HTTP(S) from ${each.value.cidr}"
}

resource "aws_security_group_rule" "alb_egress" {
  type              = "egress"
  security_group_id = aws_security_group.alb.id
  protocol          = "-1"
  from_port         = 0
  to_port           = 0
  cidr_blocks       = ["0.0.0.0/0"]
}

resource "aws_security_group" "ecs" {
  name        = "${local.name_prefix}-ecs"
  description = "Terraducktel Fargate tasks"
  vpc_id      = aws_vpc.this.id
  tags        = { Name = "${local.name_prefix}-ecs-sg" }
}

resource "aws_security_group_rule" "ecs_api_from_alb" {
  type                     = "ingress"
  security_group_id        = aws_security_group.ecs.id
  protocol                 = "tcp"
  from_port                = 8000
  to_port                  = 8000
  source_security_group_id = aws_security_group.alb.id
  description              = "API from ALB"
}

resource "aws_security_group_rule" "ecs_ui_from_alb" {
  type                     = "ingress"
  security_group_id        = aws_security_group.ecs.id
  protocol                 = "tcp"
  from_port                = 8080
  to_port                  = 8080
  source_security_group_id = aws_security_group.alb.id
  description              = "UI from ALB"
}

# Tasks talk to each other (api <-> detectors <-> executor) on the internal
# network, e.g. executor -> http://<api>:8000 for state + run callbacks.
resource "aws_security_group_rule" "ecs_self" {
  type              = "ingress"
  security_group_id = aws_security_group.ecs.id
  protocol          = "-1"
  from_port         = 0
  to_port           = 0
  self              = true
  description       = "Intra-task"
}

resource "aws_security_group_rule" "ecs_egress" {
  type              = "egress"
  security_group_id = aws_security_group.ecs.id
  protocol          = "-1"
  from_port         = 0
  to_port           = 0
  cidr_blocks       = ["0.0.0.0/0"]
}

resource "aws_security_group" "rds" {
  name        = "${local.name_prefix}-rds"
  description = "Terraducktel RDS Postgres"
  vpc_id      = aws_vpc.this.id
  tags        = { Name = "${local.name_prefix}-rds-sg" }
}

resource "aws_security_group_rule" "rds_from_ecs" {
  type                     = "ingress"
  security_group_id        = aws_security_group.rds.id
  protocol                 = "tcp"
  from_port                = 5432
  to_port                  = 5432
  source_security_group_id = aws_security_group.ecs.id
  description              = "Postgres from ECS tasks"
}
