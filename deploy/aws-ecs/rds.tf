resource "aws_db_subnet_group" "this" {
  name       = "${local.name_prefix}-db"
  subnet_ids = aws_subnet.private[*].id
  tags       = { Name = "${local.name_prefix}-db-subnets" }
}

# Force TLS for all connections (the DATABASE_URL uses ?ssl=require to match).
resource "aws_db_parameter_group" "this" {
  name   = "${local.name_prefix}-pg16"
  family = "postgres16"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  tags = { Name = "${local.name_prefix}-pg16" }
}

resource "aws_db_instance" "this" {
  identifier     = "${local.name_prefix}-db"
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.db_instance_class

  db_name  = "terraducktel"
  username = "terraducktel"
  password = var.db_password

  allocated_storage     = 20
  max_allocated_storage = 200
  storage_encrypted     = true
  storage_type          = "gp3"

  db_subnet_group_name   = aws_db_subnet_group.this.name
  parameter_group_name   = aws_db_parameter_group.this.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  multi_az               = false

  backup_retention_period         = 7
  backup_window                   = "03:00-04:00"
  maintenance_window              = "sun:04:00-sun:05:00"
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  deletion_protection       = true
  skip_final_snapshot       = false
  final_snapshot_identifier = "${local.name_prefix}-db-final"

  # pgcrypto is required by the audit-log hash-chain migration; the app runs
  # `CREATE EXTENSION IF NOT EXISTS pgcrypto` on migrate, so no parameter-group
  # preload is needed.

  tags = { Name = "${local.name_prefix}-db" }
}
