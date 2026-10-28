# Offline tests: the AWS provider is MOCKED, so no AWS account, credentials
# or network calls are used and nothing is created.
#   tofu -chdir=infra test

mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }

  mock_data "aws_availability_zones" {
    defaults = {
      names = ["us-west-2a", "us-west-2b", "us-west-2c"]
    }
  }

  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{}"
    }
  }
}

run "defaults_create_secure_network_and_bucket" {
  command = plan

  assert {
    condition     = length(aws_subnet.private) == 2
    error_message = "Expected 2 private subnets by default."
  }

  assert {
    condition     = alltrue([for s in aws_subnet.private : s.map_public_ip_on_launch == false])
    error_message = "Subnets must not assign public IPs."
  }

  assert {
    condition     = aws_subnet.private[0].cidr_block == "10.20.0.0/20" && aws_subnet.private[1].cidr_block == "10.20.16.0/20"
    error_message = "Unexpected subnet CIDR layout."
  }

  assert {
    condition     = aws_subnet.private[0].availability_zone != aws_subnet.private[1].availability_zone
    error_message = "Subnets must be in different AZs."
  }

  assert {
    condition     = aws_s3_bucket.artifacts.bucket == "healthqueue-dev-artifacts-123456789012"
    error_message = "Unexpected artifacts bucket name."
  }

  assert {
    condition = alltrue([
      aws_s3_bucket_public_access_block.artifacts.block_public_acls,
      aws_s3_bucket_public_access_block.artifacts.block_public_policy,
      aws_s3_bucket_public_access_block.artifacts.ignore_public_acls,
      aws_s3_bucket_public_access_block.artifacts.restrict_public_buckets,
    ])
    error_message = "All S3 public access blocks must be enabled."
  }

  assert {
    condition     = aws_s3_bucket_versioning.artifacts.versioning_configuration[0].status == "Enabled"
    error_message = "Bucket versioning must be enabled."
  }

  assert {
    condition     = one(aws_s3_bucket_server_side_encryption_configuration.artifacts.rule).apply_server_side_encryption_by_default[0].sse_algorithm == "AES256"
    error_message = "Bucket must be encrypted."
  }

  assert {
    condition     = aws_vpc_endpoint.s3.service_name == "com.amazonaws.us-west-2.s3" && aws_vpc_endpoint.s3.vpc_endpoint_type == "Gateway"
    error_message = "S3 endpoint must be a (free) Gateway endpoint."
  }

  assert {
    condition     = aws_s3_bucket.artifacts.force_destroy == true
    error_message = "Non-prod buckets should allow force_destroy for easy cleanup."
  }
}

run "prod_protects_bucket_and_three_azs" {
  command = plan

  variables {
    environment = "prod"
    az_count    = 3
  }

  assert {
    condition     = aws_s3_bucket.artifacts.force_destroy == false
    error_message = "Prod bucket must not be force-destroyable."
  }

  assert {
    condition     = length(aws_subnet.private) == 3
    error_message = "Expected 3 private subnets."
  }
}

run "rejects_invalid_environment" {
  command = plan

  variables {
    environment = "production"
  }

  expect_failures = [var.environment]
}

run "rejects_invalid_cidr" {
  command = plan

  variables {
    vpc_cidr = "not-a-cidr"
  }

  expect_failures = [var.vpc_cidr]
}

run "rejects_single_az" {
  command = plan

  variables {
    az_count = 1
  }

  expect_failures = [var.az_count]
}
