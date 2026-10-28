# HealthQueue Architecture

## 1. System overview

```
 Developer ──PR──► GitHub (main, protected)
                     │
                     ▼
            GitHub Actions (ci.yml)
   ┌────────────┬────────────────┬───────────────────┬───────────────────────────┐
   │ test       │ validate-config│ build-and-scan    │ publish-and-sign (main)   │
   │ pytest     │ tofu fmt/valid/│ docker build      │ push → GHCR               │
   │            │ test (mocked), │ Trivy gate        │ Cosign sign (OIDC)        │
   │            │ kubeconform,   │ Syft SBOM         │ SBOM attest, SLSA prov.   │
   │            │ Trivy config   │                   │ cosign verify             │
   └────────────┴────────────────┴───────────────────┴─────────────┬─────────────┘
                                                                   ▼
                                                 ghcr.io/katie-csp/cst365_week7
                                                                   │ pull
   GitHub repo (k8s/base) ──watch main──► Argo CD ──sync──► Kubernetes namespace "healthqueue"
                                                             Deployment (1 pod, Gunicorn+Flask)
                                                             Service (ClusterIP :80 → :5000)

   OpenTofu (infra/) ──► AWS: VPC + private subnets + S3 endpoint + artifacts bucket
                         State: encrypted, versioned S3 bucket with S3-native locking
```

## 2. Components

| Area | Files | Responsibility |
|---|---|---|
| **API** | `app.py` | Flask app factory, the `/health` and `/appointments` endpoints, validation, JSON errors, 16 KB body limit |
| **Tests** | `test_app.py`, `test_dora_metrics.py`, `infra/tests/` | API behaviour, DORA calculations, and infrastructure logic (mocked AWS) |
| **Container** | `Dockerfile`, `.dockerignore` | Multi-stage, non-root, read-only-ready image with no packaging tools |
| **CI/CD** | `.github/workflows/ci.yml`, `.github/dependabot.yml` | Test, validate, scan, SBOM, publish, sign, attest; automated updates |
| **Infrastructure** | `infra/` | VPC, private subnets, S3 gateway endpoint, locked default security group, artifacts bucket; state bootstrap |
| **Deployment** | `k8s/` | Kustomize base and local overlay; Argo CD AppProject and Application |
| **Operations** | `OBSERVABILITY.md`, `TROUBLESHOOTING.md` | SLOs, alerts, runbook, debugging |
| **Delivery metrics** | `dora_metrics.py`, `DORA_ANALYSIS.md` | The four DORA metrics (synthetic data) |
| **Decisions** | `adr/` | Why each major choice was made |

## 3. Request flow: booking an appointment

1. The client sends `POST /appointments` with JSON (at most 16 KB, otherwise **413**).
2. Flask parses the JSON. If it's invalid, the API returns **400** "Request body must be valid JSON."
3. `validate_appointment()` checks all fields and returns **every** error at once (**400** with a `details` list).
4. The appointment is stored in memory with an ID and `status: "scheduled"`, and the API returns **201**.

In Kubernetes, the request goes from the Service (port 80) to the pod (port 5000), where Gunicorn runs the Flask app. The pod only receives traffic after its readiness probe on `/health` passes.

## 4. Delivery flow

1. **Code change:** a PR to `main`. CI runs `test`, `validate-config` and `build-and-scan`, and branch protection requires them to pass.
2. **Merge:** `publish-and-sign` pushes the *exact scanned image* to GHCR by digest, signs it keylessly, and attaches the SBOM and SLSA provenance.
3. **Release:** a PR changes `newTag` in `k8s/base/kustomization.yaml`. After the merge, Argo CD syncs automatically, and the rolling update (`maxUnavailable: 0`) avoids downtime.
4. **Rollback:** revert the PR. Argo CD's self-heal undoes any manual `kubectl` changes.
5. **Infrastructure:** `tofu plan`, review, `tofu apply`, run by a person with AWS SSO credentials. State lives in S3 and is locked during each run.

## 5. Security layers

| Layer | Controls |
|---|---|
| **Code** | Input validation, JSON errors with no stack traces, debug mode off by default, body size limit |
| **Dependencies** | Pinned versions, Dependabot with a cooldown, Trivy vulnerability gate |
| **Build** | SHA-pinned actions, least-privilege job permissions, no stored secrets, keyless signing, SBOM, SLSA provenance |
| **Image** | Digest-pinned slim base, non-root uid 10001, no pip/setuptools, allowlist `.dockerignore` |
| **Runtime** | Pod Security restricted, read-only root filesystem, capabilities dropped, seccomp, no service-account token, resource limits |
| **GitOps** | Dedicated Argo CD AppProject (one repo, one namespace, four resource kinds), protected `main` |
| **Cloud** | Private subnets only, all S3 public access blocked, TLS-only bucket policies, encryption, versioning, least-privilege state IAM policy |
| **Data** | Synthetic only; telemetry allowlists ([OBSERVABILITY.md](OBSERVABILITY.md) §7) |

Details: [SUPPLY_CHAIN_SECURITY.md](SUPPLY_CHAIN_SECURITY.md).

## 6. Key design decisions

| ADR | Decision |
|---|---|
| [ADR-001](adr/ADR-001-infrastructure-as-code.md) | OpenTofu on AWS, S3 remote state with native locking, private-only VPC |
| [ADR-002](adr/ADR-002-flask-in-memory-single-replica.md) | Flask with in-memory storage and a single replica |
| [ADR-003](adr/ADR-003-container-supply-chain.md) | Hardened image, keyless Cosign signing, SBOM, provenance |
| [ADR-004](adr/ADR-004-gitops-argocd-kustomize.md) | GitOps with Argo CD and Kustomize |
| [ADR-005](adr/ADR-005-slo-based-observability.md) | SLO-based observability and DORA measurement |

## 7. Known gaps and roadmap

1. **Persistence:** move to PostgreSQL, then add idempotency keys and more replicas.
2. **Observability:** implement the stack from `OBSERVABILITY.md` (metrics, JSON logs, OpenTelemetry, alerts).
3. **Image pinning:** pin the image to `sha-<commit>`, with an automated tag-bump PR from CI.
4. **Admission control:** a default-deny NetworkPolicy, and Kyverno or Sigstore policy-controller to allow only signed images.
5. **CI to AWS:** a GitHub OIDC role for AWS, so CI can run `tofu plan` on PRs.
6. **Authentication:** add authentication and authorization to the API.
