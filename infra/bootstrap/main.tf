# ---------------------------------------------------------------------------
# BOOTSTRAP: creates the S3 bucket that stores OpenTofu remote state.
#
# This is the classic chicken-and-egg step: the state bucket must exist
# before any configuration can use it, so this small config uses LOCAL
# state and is applied once by an administrator. See infra/README.md.
# ---------------------------------------------------------------------------

terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

variable "aws_region" {
  description = "AWS region for the state bucket."
  type        = string
  default     = "us-west-2"
}

variable "project" {
  description = "Project name used in resource names and tags."
  type        = string
  default     = "healthqueue"
}

variable "noncurrent_version_retention_days" {
  description = "How long to keep old state versions (for recovery) before deleting them."
  type        = number
  default     = 90
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "OpenTofu"
      Purpose   = "opentofu-remote-state"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  # Account ID + region make the globally-unique bucket name predictable.
  state_bucket_name = "${var.project}-tfstate-${data.aws_caller_identity.current.account_id}-${var.aws_region}"
}

# Accepted risk (ADR-001): access logging needs a separate log bucket - add before storing anything sensitive.
# trivy:ignore:AWS-0089
resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket_name

  # Losing state is very painful - block accidental `tofu destroy`.
  lifecycle {
    prevent_destroy = true
  }
}

# Versioning lets you recover from a corrupted or bad state write.
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Encrypt every object at rest (SSE-S3, no extra cost).
# Accepted risk (ADR-001): SSE-S3 is free; a customer-managed KMS key adds ~$1/month + request costs.
# trivy:ignore:AWS-0132
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# State can contain sensitive values - it must never be public.
resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Disable ACLs entirely; access is controlled only by IAM + bucket policy.
resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Deny any request that is not over HTTPS.
data "aws_iam_policy_document" "state_bucket" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.state.arn,
      "${aws_s3_bucket.state.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.state_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.state]
}

# Clean up old state versions and failed uploads to control cost.
resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"
    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_retention_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.state]
}

# Least-privilege IAM policy for whoever runs `tofu plan/apply` on HealthQueue.
# Attach it to an IAM role (recommended) or user - see infra/README.md.
data "aws_iam_policy_document" "state_access" {
  statement {
    sid       = "ListStateBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${var.project}/*"]
    }
  }

  statement {
    sid     = "ReadWriteStateAndLockFiles"
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    # DeleteObject is needed to release the .tflock file.
    resources = ["${aws_s3_bucket.state.arn}/${var.project}/*"]
  }
}

resource "aws_iam_policy" "state_access" {
  name        = "${var.project}-tofu-state-access"
  description = "Read/write ${var.project} OpenTofu state and lock files only."
  policy      = data.aws_iam_policy_document.state_access.json
}

output "state_bucket_name" {
  description = "Put this value in infra/backend.hcl as `bucket`."
  value       = aws_s3_bucket.state.bucket
}

output "state_access_policy_arn" {
  description = "Attach this IAM policy to the role/user that runs OpenTofu."
  value       = aws_iam_policy.state_access.arn
}
