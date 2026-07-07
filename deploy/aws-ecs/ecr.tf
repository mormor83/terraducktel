# One ECR repo per TDT image. Push here, then set var.image_tag and apply.
resource "aws_ecr_repository" "this" {
  for_each             = toset(["api", "ui", "drift-detector", "liveness-detector", "executor"])
  name                 = "${local.name_prefix}-${each.key}"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Name = "${local.name_prefix}-${each.key}" }
}

# Keep only the last 20 images per repo.
resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 20 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 20
      }
      action = { type = "expire" }
    }]
  })
}
