# HealthQueue Infrastructure (OpenTofu + AWS)

> **Nothing in this folder has been deployed.** It has been checked offline only: `tofu fmt`, `tofu validate`, `tofu test` with a mocked AWS provider, and a Trivy misconfiguration scan. You create real resources only when *you* run `tofu apply`.

## What it creates

| Config | Resources | Purpose |
|---|---|---|
| `bootstrap/` (run once, local state) | S3 state bucket (versioned, encrypted, private, TLS-only, old versions expire after 90 days) and an IAM policy for state access | Stores OpenTofu remote state |
| `infra/` (remote state) | VPC, 2 private subnets in different AZs, a route table, a free S3 gateway endpoint, the default security group with all rules removed, and an artifacts S3 bucket (versioned, encrypted, private, TLS 1.2+ only, lifecycle rules) | HealthQueue network and storage for build artifacts (SBOMs, scan reports). **No PHI, ever.** |

Every resource is tagged with `Project`, `Environment`, `Owner`, `ManagedBy=OpenTofu` and `DataClassification=synthetic-only`.

**State locking** uses OpenTofu's S3-native lock file (`use_lockfile = true`, OpenTofu 1.10+). A `.tflock` object sits next to the state, so no DynamoDB table is needed.

## Cost estimate

| Item | Cost |
|---|---|
| VPC, subnets, route table, security group, S3 gateway endpoint | **Free** |
| S3 state and artifacts buckets | A few KB to MB: **about $0.01/month** (S3 Standard is about $0.023/GB-month, plus tiny request costs) |
| IAM policy | Free |
| **Deliberately NOT created** | NAT Gateway (about $32+/month plus data charges), interface endpoints (about $7/month each), KMS customer-managed keys ($1/month each), VPC Flow Logs (storage/ingestion) |

Expect **under $1/month**. The AWS Free Tier may cover it. Set a **budget alert** anyway: Billing → Budgets → Create budget → "Zero spend" or $5.

## Prerequisites

1. **OpenTofu ≥ 1.10** (`tofu version`).
2. An **AWS account** with MFA on the root user. Don't use root for daily work.
3. **Credentials outside the repo.** Use one of these:
   - **IAM Identity Center (SSO)**, recommended: `aws configure sso`, then `aws sso login --profile healthqueue`, then `$env:AWS_PROFILE = "healthqueue"`
   - An IAM user's access keys in `~/.aws/credentials` (never in this folder)
   - For CI: GitHub OIDC → IAM role (no stored keys), just as the image signing works

   OpenTofu reads credentials from your environment or AWS profile. **No `.tf`, `.tfvars` or `.hcl` file here contains credentials**, and `.gitignore` blocks state, `*.tfvars`, `backend.hcl` and `.terraform/`.

## Initial setup (one time)

```powershell
# 1. Create the state bucket (uses LOCAL state, applied once by an admin)
tofu -chdir=infra/bootstrap init
tofu -chdir=infra/bootstrap plan      # review: 7 resources to add
tofu -chdir=infra/bootstrap apply
tofu -chdir=infra/bootstrap output    # note state_bucket_name and state_access_policy_arn
```

2. **Attach the IAM policy** `healthqueue-tofu-state-access` (from the output) to the role or user that will run OpenTofu. It allows access only to `healthqueue/*` state and lock files.
3. Keep the bootstrap's local `terraform.tfstate` safe. It's git-ignored, so back it up privately, or migrate it into the bucket later.

```powershell
# 4. Configure the backend for the main stack
Copy-Item infra/backend.hcl.example infra/backend.hcl     # then edit the bucket name
Copy-Item infra/terraform.tfvars.example infra/terraform.tfvars   # optional overrides

# 5. Initialize with remote state, then plan/apply
tofu -chdir=infra init -backend-config=backend.hcl
tofu -chdir=infra plan -out=tfplan
tofu -chdir=infra apply tfplan
```

For other environments, use a different `key` in the backend file (e.g. `healthqueue/staging/terraform.tfstate`) and `environment = "staging"`.

## Verify without AWS (what has been run)

```powershell
tofu fmt -check -recursive infra
tofu -chdir=infra init -backend=false
tofu -chdir=infra validate
tofu -chdir=infra test            # mocked AWS provider - no account needed
```

## Tear down

```powershell
tofu -chdir=infra destroy
```

The state bucket has `prevent_destroy`. To remove it, delete that lifecycle block, empty all object versions, then run `tofu -chdir=infra/bootstrap destroy`.
