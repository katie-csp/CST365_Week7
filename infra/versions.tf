terraform {
  # 1.10+ is required for S3-native state locking (use_lockfile).
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Remote state in the encrypted, versioned bucket created by infra/bootstrap.
  # `bucket`, `key` and `region` come from backend.hcl (partial configuration),
  # so no account-specific values are hard-coded here.
  #   tofu init -backend-config=backend.hcl
  backend "s3" {
    encrypt      = true # server-side encryption for the state object
    use_lockfile = true # S3-native locking via a .tflock object - no DynamoDB needed
  }
}
