# ADR-001: OpenTofu on AWS with S3 remote state

- **Status:** Accepted
- **Date:** 2026-10-28

## Context
HealthQueue needs reproducible, reviewable cloud infrastructure for a DevOps capstone on a student budget. It only ever handles synthetic data.

## Decision
1. **OpenTofu** (open source, MPL-2.0) manages the infrastructure as code with the `hashicorp/aws ~> 6.0` provider. The `.terraform.lock.hcl` file pins provider checksums.
2. **Remote state** lives in an S3 bucket that is versioned, encrypted (SSE-S3), blocks all public access, has ACLs disabled and denies non-TLS requests. The bucket is created by a separate `bootstrap` config that has `prevent_destroy`.
3. **Locking** uses OpenTofu's S3-native `use_lockfile`, not DynamoDB. This means fewer resources, no extra cost, and it's the current recommended approach.
4. **Network:** a VPC with **private subnets only** across 2 AZs, a free S3 gateway endpoint, and a default security group with no rules. There's **no NAT gateway or internet gateway** yet.
5. **Credentials** never live in the repo. People use AWS SSO or profiles; CI should use GitHub OIDC with an IAM role.

## Accepted risks (Trivy findings suppressed inline with a reason)
| Finding | Reason | Revisit when |
|---|---|---|
| AWS-0132: no customer-managed KMS key | SSE-S3 is free and encrypts at rest; a CMK costs about $1/month per key | Before any real or regulated data, or for prod |
| AWS-0089: no S3 access logging | Needs a separate log bucket | Before prod |
| AWS-0178: no VPC Flow Logs | No workloads in the VPC yet; logs have ongoing cost | When compute (e.g. ECS) is added |

## Consequences
- **Positive:** costs under about $1/month, secure defaults, state can be recovered from old versions, and config can be tested offline with `tofu test`.
- **Negative:** the bootstrap stack has local state that must be backed up manually. Private-only subnets can't reach the internet, so running the container (e.g. pulling from GHCR) will later need a NAT gateway or VPC interface endpoints, at extra cost.
- **Alternatives rejected:**
  - Terraform: BSL license.
  - DynamoDB locking: deprecated in favour of the S3 lock file.
  - Public subnets: unnecessary exposure.
  - A NAT gateway now: about $32/month with no workload to use it.
