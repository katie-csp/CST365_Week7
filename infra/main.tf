# HealthQueue AWS infrastructure
#   1. VPC with private subnets across AZs (no NAT gateway, no internet gateway)
#   2. Free S3 gateway endpoint so private subnets can reach S3
#   3. Locked-down default security group
#   4. Private, encrypted, versioned S3 bucket for build artifacts (SBOMs, scan reports)
#
# DATA POLICY: synthetic data only. Never store real patient information (PHI).

provider "aws" {
  region = var.aws_region

  # Every resource gets these tags automatically.
  default_tags {
    tags = merge(
      {
        Project            = var.project
        Environment        = var.environment
        Owner              = var.owner
        ManagedBy          = "OpenTofu"
        DataClassification = "synthetic-only"
      },
      var.extra_tags,
    )
  }
}

data "aws_caller_identity" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name_prefix = "${var.project}-${var.environment}"
  azs         = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  # Carve /20 private subnets from the VPC range: 10.20.0.0/20, 10.20.16.0/20, ...
  private_subnet_cidrs = [for i in range(var.az_count) : cidrsubnet(var.vpc_cidr, 4, i)]

  # Account ID keeps the bucket name globally unique without random suffixes.
  artifacts_bucket_name = "${local.name_prefix}-artifacts-${data.aws_caller_identity.current.account_id}"
}

# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------

# Accepted risk (ADR-001): no workloads run in the VPC yet; Flow Logs add storage/ingestion cost.
# trivy:ignore:AWS-0178
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${local.name_prefix}-vpc" }
}

resource "aws_subnet" "private" {
  count = var.az_count

  vpc_id                  = aws_vpc.main.id
  cidr_block              = local.private_subnet_cidrs[count.index]
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = false # nothing gets a public IP

  tags = {
    Name = "${local.name_prefix}-private-${local.azs[count.index]}"
    Tier = "private"
  }
}

# Security: no 0.0.0.0/0 route on purpose - there is no path to or from the
# internet, only to S3 through the gateway endpoint below.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name_prefix}-private-rt" }
}

resource "aws_route_table_association" "private" {
  count = var.az_count

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# Gateway endpoints are free and keep S3 traffic on the AWS network.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = { Name = "${local.name_prefix}-s3-endpoint" }
}

# Take control of the default security group and remove all its rules,
# so nothing accidentally launched with it can talk to anything.
resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name_prefix}-default-sg-locked" }
}

# ---------------------------------------------------------------------------
# Artifacts bucket (SBOMs, Trivy reports, build outputs - never PHI)
# ---------------------------------------------------------------------------

# Accepted risk (ADR-001): access logging needs a separate log bucket - add before storing anything sensitive.
# trivy:ignore:AWS-0089
resource "aws_s3_bucket" "artifacts" {
  bucket = local.artifacts_bucket_name

  # Prevent destroying a bucket that still holds objects in prod.
  force_destroy = var.environment != "prod"

  tags = { Name = local.artifacts_bucket_name }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    object_ownership = "BucketOwnerEnforced" # ACLs disabled
  }
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Accepted risk (ADR-001): SSE-S3 is free; a customer-managed KMS key adds ~$1/month + request costs.
# trivy:ignore:AWS-0132
resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.artifact_noncurrent_version_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.artifacts]
}

data "aws_iam_policy_document" "artifacts" {
  # Require HTTPS for every request.
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.artifacts.arn,
      "${aws_s3_bucket.artifacts.arn}/*",
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

  # Reject old, weaker TLS versions.
  statement {
    sid     = "DenyOutdatedTLS"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.artifacts.arn,
      "${aws_s3_bucket.artifacts.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "NumericLessThan"
      variable = "s3:TlsVersion"
      values   = ["1.2"]
    }
  }
}

resource "aws_s3_bucket_policy" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  policy = data.aws_iam_policy_document.artifacts.json

  depends_on = [aws_s3_bucket_public_access_block.artifacts]
}
