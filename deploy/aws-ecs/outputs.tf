output "alb_dns_name" {
  description = "ALB DNS name. Point domain_name at this (or set route53_zone_id)."
  value       = aws_lb.this.dns_name
}

output "app_url" {
  description = "Entry point for the TDT UI."
  value       = "https://${var.domain_name}/"
}

output "rds_endpoint" {
  description = "RDS Postgres host:port (reachable from inside the VPC only)."
  value       = aws_db_instance.this.endpoint
}

output "tfstate_bucket" {
  description = "S3 bucket TDT writes user Terraform state to."
  value       = aws_s3_bucket.tfstate.id
}

output "ecr_repository_urls" {
  description = "Push images here, then set image_tag and apply."
  value       = { for k, r in aws_ecr_repository.this : k => r.repository_url }
}

output "ecs_cluster_arn" {
  value = aws_ecs_cluster.this.arn
}
