data "aws_caller_identity" "current" {}
data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  account_id  = data.aws_caller_identity.current.account_id
  name_prefix = "${var.name_prefix}-${var.environment}"

  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  # /20 subnets carved from the VPC CIDR: public first, then private.
  public_subnet_cidrs  = [for i in range(var.az_count) : cidrsubnet(var.vpc_cidr, 4, i)]
  private_subnet_cidrs = [for i in range(var.az_count) : cidrsubnet(var.vpc_cidr, 4, i + 8)]

  ecr_base = "${local.account_id}.dkr.ecr.${var.aws_region}.amazonaws.com"

  # TDT service images pushed to the ECR repos created by ecr.tf.
  images = {
    api               = "${local.ecr_base}/${local.name_prefix}-api:${var.image_tag}"
    ui                = "${local.ecr_base}/${local.name_prefix}-ui:${var.image_tag}"
    drift-detector    = "${local.ecr_base}/${local.name_prefix}-drift-detector:${var.image_tag}"
    liveness-detector = "${local.ecr_base}/${local.name_prefix}-liveness-detector:${var.image_tag}"
    executor          = "${local.ecr_base}/${local.name_prefix}-executor:${var.image_tag}"
  }

  tags = {
    Environment = var.environment
    ManagedBy   = "terraform"
    Service     = "terraducktel"
  }
}
