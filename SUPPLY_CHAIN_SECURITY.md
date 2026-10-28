# HealthQueue Supply-Chain Security

This document explains how the CI/CD pipeline (`.github/workflows/ci.yml`) protects the software supply chain and how it maps to **SLSA** (Supply-chain Levels for Software Artifacts, v1.0).

## Whole-project security summary

| Area | Control | Where |
|---|---|---|
| **Secrets** | None stored in the repo or CI. GHCR uses `GITHUB_TOKEN`, signing uses GitHub OIDC, AWS uses SSO/profiles. `.gitignore` blocks state, `*.tfvars`, `backend.hcl`, `.env`, keys | `.gitignore`, `ci.yml`, `infra/README.md` |
| **Source** | Protected `main` (PR + required checks); Dependabot with a 7-day cooldown | GitHub settings, `.github/dependabot.yml` |
| **Dependencies** | Exact Python versions; base image pinned by digest; actions pinned by SHA; OpenTofu provider checksums in `.terraform.lock.hcl` | `requirements*.txt`, `Dockerfile`, `ci.yml`, `infra/` |
| **Build** | Least-privilege jobs; PRs can't publish; script-injection-safe `env:` usage; `actionlint` + `zizmor` clean | `ci.yml` |
| **Scanning** | Trivy image gate (HIGH/CRITICAL fixable), Trivy config scans of OpenTofu and **rendered** Kubernetes manifests, kubeconform schemas, OpenTofu tests | `ci.yml` (`validate-config`, `build-and-scan`) |
| **Exceptions** | Every suppression has a written reason; Kubernetes ones expire (`.trivyignore`), OpenTofu ones are inline and recorded in ADR-001 | `.trivyignore`, `infra/*.tf` |
| **Artifacts** | SPDX SBOM (Syft), Cosign keyless signature + SBOM attestation, SLSA provenance, all bound to the image **digest** | `ci.yml` |
| **Image** | Multi-stage, non-root uid 10001, no pip/setuptools/wheel, allowlist `.dockerignore`, HEALTHCHECK | `Dockerfile` |
| **Runtime** | Pod Security restricted, read-only root filesystem, capabilities dropped, seccomp, no service-account token, resource limits | `k8s/base/` |
| **Deployment** | Argo CD AppProject limited to one repo, one namespace and four resource kinds; Git is the only deploy path | `k8s/argocd/` |
| **Cloud** | Private-only VPC, S3 public access blocked, TLS-only policies, encryption, versioning, least-privilege state IAM policy | `infra/` |
| **Data** | Synthetic data only; telemetry allowlists, no PHI in logs, metrics or traces | `OBSERVABILITY.md` §7 |

**Residual risks (tracked):**
- The image is `:latest` until the first publish (exception **expires 2026-12-31**).
- No admission check that only signed images may run.
- No NetworkPolicy.
- No API authentication.
- No customer-managed KMS keys, access logs or flow logs (see ADR-001).
- SLSA Build L2, not yet L3.

## Pipeline overview

```
 push / PR
    │
    ▼
┌─────────┐   ┌──────────────────────────────┐   ┌───────────────────────────────────┐
│  test   │──►│ build-and-scan               │──►│ publish-and-sign (main only)      │
│ pytest  │   │ • docker build (pinned base) │   │ • load the SAME scanned image     │
│         │   │ • Trivy → SARIF report       │   │ • push to GHCR (GITHUB_TOKEN)     │
│         │   │ • Trivy gate HIGH/CRITICAL   │   │ • Cosign keyless sign (OIDC)      │
│         │   │ • Syft SBOM (SPDX JSON)      │   │ • Cosign SBOM attestation         │
│         │   │ • save image as artifact     │   │ • SLSA provenance (actions/attest)│
└─────────┘   └──────────────────────────────┘   │ • cosign verify (self-check)      │
                                                 └───────────────────────────────────┘
```

| Stage | Tool | Purpose |
|---|---|---|
| Test | pytest | Stops broken code before it is packaged |
| Scan | Trivy | Finds known vulnerabilities (CVEs). The build fails on fixable HIGH/CRITICAL findings |
| SBOM | Syft | Lists every package in the image (SPDX format) |
| Sign | Cosign (Sigstore) | Proves the image came from this workflow and has not been changed |
| Provenance | `actions/attest` | Signed SLSA record of *how* and *where* the image was built |

## Controls

### Authentication without stored secrets
- **No repository secrets are needed.** GHCR login uses the automatic, short-lived `GITHUB_TOKEN`, which expires when the job ends.
- **Keyless signing with GitHub OIDC.** The job requests an OIDC identity token (`id-token: write`). Sigstore's Fulcio swaps it for a signing certificate that lasts about 10 minutes and names this repo, workflow and branch. There are no private keys to store, rotate or leak.
- Signatures are recorded in Sigstore's public **Rekor** transparency log, so tampering is detectable.

> **Privacy note:** Rekor is public. It records the repository and workflow name, but never code or data. The app itself only ever uses synthetic data.

### Least-privilege permissions
- The workflow default is `permissions: {}` (nothing). Each job asks only for what it needs:

| Job | Permissions | Why |
|---|---|---|
| `test` | `contents: read` | Check out code |
| `build-and-scan` | `contents: read`, `security-events: write` | Check out code and upload the Trivy report |
| `publish-and-sign` | `packages: write`, `id-token: write`, `attestations: write` | Push, sign keylessly, store provenance |

- **Pull requests never get write access or OIDC tokens.** Publishing runs only on `push` to `main`.
- `persist-credentials: false` stops the checkout token from being saved to disk, where later steps could read it.
- GitHub context values reach scripts through `env:` variables instead of being pasted into the script text, which prevents script injection.

### Pinned, reviewed dependencies
- Every action is pinned to a **full commit SHA**, with its version tag in a comment. Tags can be moved by an attacker; SHAs can't. Each SHA was resolved from the official repo's release tag.
- The chosen action releases were at least ~2 weeks old, a "cooldown" so a malicious release is likely to be caught before we adopt it. Dependabot uses a 7-day cooldown.
- The Docker base image is pinned by **digest** (`python:3.11-slim@sha256:…`).
- Python packages are pinned to exact versions in `requirements.txt`.
- **Dependabot** (`.github/dependabot.yml`) opens PRs to update all three. Each PR goes through the full test and scan pipeline before it can be merged.

### The scanned image is the published image
`build-and-scan` saves the exact image it scanned as a workflow artifact. `publish-and-sign` loads and pushes *that* file instead of rebuilding, so nothing can change between the scan and the signature. Signing and verification always use the immutable **digest** (`@sha256:…`), never a tag.

### Image hardening
These come from the Dockerfile and are covered in the README:
- Multi-stage build on a slim base image.
- Runs as a non-root user (uid 10001).
- An allowlist `.dockerignore`, so no `.env`, `.git`, tests or data files get into the image.
- No test tools in the production image.

### Workflow linting and config validation
- The workflow is checked with `actionlint` (syntax, expressions and shellcheck) and `zizmor` (GitHub Actions security audit, pedantic mode).
- The `validate-config` job checks OpenTofu (`fmt`, `validate`, and `test` with a mocked AWS provider), validates the rendered Kubernetes manifests with kubeconform, and runs Trivy misconfiguration scans on both.
- It needs no cloud credentials, so it can never change real infrastructure.

## SLSA alignment (Build track, v1.0)

| Requirement | Level | How HealthQueue meets it | Status |
|---|---|---|---|
| Provenance exists | L1 | `actions/attest` creates SLSA v1 provenance (source repo, commit, workflow, runner) for the image digest | ✅ (public repos*) |
| Build runs on a hosted platform | L2 | GitHub-hosted runners, not a developer laptop | ✅ |
| Provenance is authentic (signed) | L2 | The provenance is signed by Sigstore using the workflow's OIDC identity and pushed to GHCR next to the image | ✅ |
| Provenance cannot be forged by the build steps | L3 | **Partial.** The signing runs in the same job as the build steps. Full L3 needs signing isolated from user-controlled steps, e.g. GitHub's reusable-workflow approach or the `slsa-github-generator` | ⚠️ L2 → L3 path |
| Builds are isolated from each other | L3 | Each job runs on a fresh, short-lived runner VM | ✅ |

\* GitHub artifact attestations are free for **public** repos. On a private repo (without GitHub Enterprise Cloud) the provenance step is skipped automatically, and Cosign signing plus the SBOM attestation still run.

**Overall: this pipeline meets SLSA Build Level 2**, with most Level 3 properties in place. To reach L3, move the build and attestation into a reusable workflow so the signing identity is separate from the calling repository's steps.

Supporting practices outside the SLSA Build track: SBOM generation, vulnerability gating, pinned dependencies, branch protection (recommended below) and two-person review.

## Verifying a published image

Anyone can check the image with Cosign or the GitHub CLI (replace `OWNER/REPO`, lowercase):

```bash
IMAGE=ghcr.io/OWNER/REPO:latest

# 1. Signature: was it signed by our workflow, on main?
cosign verify "$IMAGE" \
  --certificate-identity-regexp '^https://github.com/OWNER/REPO/\.github/workflows/ci\.yml@refs/heads/main$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

# 2. SBOM attestation
cosign verify-attestation "$IMAGE" --type spdxjson \
  --certificate-identity-regexp '^https://github.com/OWNER/REPO/\.github/workflows/ci\.yml@refs/heads/main$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

# 3. SLSA provenance
gh attestation verify "oci://$IMAGE" --repo OWNER/REPO
```

## Known limitations
- The vulnerability gate ignores vulnerabilities **with no fix available** (`ignore-unfixed`) to avoid blocking on issues no one can patch. They still appear in the full SARIF report.
- Trivy's vulnerability database is downloaded at scan time, so a new CVE can make a previously passing build fail. That is intended.
- Rebuilding from the same commit later may pick up different OS package metadata, so builds are pinned but not bit-for-bit reproducible.
