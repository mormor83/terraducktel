# Bucket where Terraducktel stores the Terraform state it manages on behalf of
# its users (the HTTP state backend persists here). Versioning is required —
# every TDT apply overwrites state, and recovery depends on prior versions.
resource "aws_s3_bucket" "tfstate" {
  bucket = "${local.name_prefix}-tfstate-${local.account_id}"
  tags   = { Name = "${local.name_prefix}-tfstate" }
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Allow the task role to read/write the state bucket.
data "aws_iam_policy_document" "state_bucket" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.tfstate.arn, "${aws_s3_bucket.tfstate.arn}/*"]
  }
}

resource "aws_iam_role_policy" "task_state_bucket" {
  name   = "tfstate-bucket"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.state_bucket.json
}
