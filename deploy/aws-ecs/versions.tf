terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }

  # Remote state is recommended. Wire your own backend, e.g.:
  #
  # backend "s3" {
  #   bucket       = "your-tf-state-bucket"
  #   key          = "terraducktel/us-east-1/terraform.tfstate"
  #   region       = "us-east-1"
  #   encrypt      = true
  #   use_lockfile = true   # S3-native locking (Terraform >= 1.10); no DynamoDB needed
  # }
  #
  # Left unset here so `terraform init` works out of the box with local state.
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.tags
  }
}
