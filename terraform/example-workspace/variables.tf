variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "bucket_name" {
  description = "Name for the example S3 bucket"
  type        = string
  default     = "example-tf-bucket"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "dev"
}

variable "use_localstack" {
  description = "Use LocalStack instead of real AWS (for local development)"
  type        = bool
  default     = true
}
