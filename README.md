# HealthQueue — DevOps Capstone

A small Flask appointment API with a full DevOps toolchain around it:
- **Testing:** automated tests
- **Containers:** a hardened image
- **CI/CD:** a supply-chain-secured pipeline (Trivy, Syft, Cosign, SLSA provenance)
- **Infrastructure as code:** OpenTofu for AWS
- **GitOps:** Kubernetes with Argo CD
- **Measurement:** an SLO-based observability plan, and DORA metrics

> **Synthetic data only.** HealthQueue must never store or log real patient information (PHI). All test data, example traffic, incidents and DORA data are synthetic.

## Contents
- [Project layout](#project-layout)
- [Quick start (local)](#quick-start-local)
- [API reference](#api-reference)
- [Docker](#docker-docker-desktop)
- [CI/CD pipeline](#cicd-pipeline-github-actions)
- [AWS infrastructure (OpenTofu)](#aws-infrastructure-opentofu)
- [Kubernetes + Argo CD](#kubernetes--argo-cd-gitops)
- [Observability & DORA](#observability--dora-metrics)
- [Validate everything locally](#validate-everything-locally)
- [Limitations](#limitations)

Related documents:
- [ARCHITECTURE.md](ARCHITECTURE.md): how it all fits together
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md): fixes for common problems
- [SUPPLY_CHAIN_SECURITY.md](SUPPLY_CHAIN_SECURITY.md): security controls and SLSA alignment
- [OBSERVABILITY.md](OBSERVABILITY.md): SLOs, alerts, on-call runbook, duplicate prevention
- [DORA_ANALYSIS.md](DORA_ANALYSIS.md): DORA results and recommendations
- [adr/](adr/): architecture decision records ADR-001 to ADR-005

## Project layout

```
CST365_Week7/
├── app.py                    # Flask API (routes, validation, JSON errors)
├── test_app.py               # API tests (pytest)
├── dora_metrics.py           # SYNTHETIC 90-day DORA data generator + calculator
├── test_dora_metrics.py      # DORA script tests
├── requirements.txt          # Runtime dependencies (in the Docker image)
├── requirements-dev.txt      # + pytest (local/CI only)
├── Dockerfile                # Multi-stage, non-root, digest-pinned base
├── .dockerignore             # Allowlist: only app.py + requirements.txt enter the image
├── .trivyignore              # Justified, expiring Trivy exceptions
├── .gitignore
├── .github/
│   ├── workflows/ci.yml      # test → validate → build/scan/SBOM → publish/sign/attest
│   └── dependabot.yml        # Updates for actions, pip and the base image
├── infra/                    # OpenTofu: VPC + S3 (remote state in S3); see infra/README.md
│   ├── bootstrap/            # One-time state-bucket setup
│   └── tests/                # Offline tests (mocked AWS)
├── k8s/
│   ├── base/                 # Namespace, ServiceAccount, Deployment, Service (Kustomize)
│   ├── overlays/local/       # Uses the locally built image on kind
│   └── argocd/               # AppProject + Application (auto-sync from main)
├── adr/                      # Architecture decision records
└── *.md                      # Documentation (see Contents)
```

## Quick start (local)

Requires Python 3.11+. These are Windows PowerShell commands; on macOS/Linux, activate with `source .venv/bin/activate`.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

pytest -v                 # run all tests
python app.py             # API at http://127.0.0.1:5000 (debug OFF; set $env:FLASK_DEBUG="1" to enable)
python dora_metrics.py    # SYNTHETIC DORA report
```

## API reference

| Method | Path | Description | Success |
|---|---|---|---|
| GET | `/health` | Health check (used by probes and monitoring) | 200 |
| GET | `/appointments` | List appointments | 200 |
| POST | `/appointments` | Book an appointment | 201 |
| GET | `/appointments/<id>` | Get one appointment | 200 |
| DELETE | `/appointments/<id>` | Cancel (sets `status: "cancelled"`) | 200 |

**Validation:**

| Field | Required | Rule |
|---|---|---|
| `patient_name` | Yes | Non-empty string, max 100 characters |
| `provider` | Yes | Non-empty string, max 100 characters |
| `date` | Yes | `YYYY-MM-DD` |
| `time` | Yes | `HH:MM`, 24-hour |
| `reason` | No | String, max 250 characters |

The request body is limited to 16 KB.

**Errors** always use one JSON shape:

```json
{"error": {"status": 400, "message": "Validation failed.", "details": ["'date' must use the format YYYY-MM-DD."]}}
```

The status codes are:
- **400:** bad JSON or failed validation
- **404:** unknown ID or URL
- **405:** wrong HTTP method
- **413:** request body too large
- **500:** unexpected error (details are hidden from the client)

**Example (synthetic):**

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5000/appointments -ContentType "application/json" `
  -Body '{"patient_name":"Test Patient Alpha","provider":"Dr. Synthetic","date":"2030-01-15","time":"09:30"}'
```

## Docker (Docker Desktop)

Start Docker Desktop first.

```powershell
docker build -t healthqueue-api:latest .
docker run -d --name healthqueue -p 5000:5000 --read-only --cap-drop ALL --security-opt no-new-privileges healthqueue-api:latest
Invoke-RestMethod http://localhost:5000/health
docker ps                                                      # STATUS shows (healthy) after ~10-30 s
docker logs healthqueue
docker inspect --format '{{.State.Health.Status}}' healthqueue
docker exec healthqueue id                                     # uid=10001(appuser)
docker stop healthqueue; docker rm healthqueue
```

What the run flags do:
- `--read-only`: the container's filesystem can't be modified.
- `--cap-drop ALL`: removes all extra Linux privileges.
- `--security-opt no-new-privileges`: the process can never gain more privileges.

In Docker Desktop, the **Containers** tab shows health, plus the **Logs / Inspect / Exec / Stats** tabs and the Stop and Delete buttons.

**What's in the image:**
- The image is built in two stages, and the final stage holds only `app.py` and the Python packages it needs.
- It's based on `python:3.11-slim`, pinned by digest.
- It runs as uid 10001 with Gunicorn (1 worker, 4 threads, because data is stored in memory).
- pip, setuptools and wheel are removed, and Docker's built-in HEALTHCHECK calls `/health`.
- **There are no secrets or test data in the image.**

## CI/CD pipeline (GitHub Actions)

`.github/workflows/ci.yml` runs on every push and PR to `main`:

| Job | Runs on | Does |
|---|---|---|
| `test` | push, PR | pytest (API + DORA tests) |
| `validate-config` | push, PR | OpenTofu fmt/validate/test (mocked AWS), kubeconform, Trivy config scans |
| `build-and-scan` | push, PR | Docker build, Trivy SARIF + HIGH/CRITICAL gate, Syft SBOM |
| `publish-and-sign` | **push to main only** | Push the *scanned* image to GHCR, then sign it, attest the SBOM, add SLSA provenance, and verify |

The published image is `ghcr.io/katie-csp/cst365_week7`, tagged `latest` and `sha-<commit>`.

**One-time GitHub setup (no secrets required):**
1. Keep the repo **public**. Code scanning and provenance are free for public repos.
2. **Settings → Actions → General:** set workflow permissions to **Read** only, and leave "Allow GitHub Actions to create and approve pull requests" **unchecked**.
3. **Settings → Rules:** add a ruleset for `main` that requires a PR and these checks:
   - `Run pytest`
   - `Validate IaC & manifests`
   - `Build, scan & SBOM`
4. **Settings → Code security:** enable Dependabot alerts and security updates.
5. After the first publish, make the GHCR package **public** (Package settings), so Kubernetes can pull it without credentials.

**Test the workflow:** push a branch and open a PR. The first three jobs should pass and `publish-and-sign` should show as *skipped*. Then merge to `main` and check the job summary, **Packages**, **Security → Code scanning**, and the run's artifacts. To verify a signature, see [SUPPLY_CHAIN_SECURITY.md](SUPPLY_CHAIN_SECURITY.md#verifying-a-published-image).

## AWS infrastructure (OpenTofu)

The `infra/` folder defines:
- a VPC with 2 private subnets
- a free S3 gateway endpoint
- a default security group with all rules removed
- a private, encrypted, versioned artifacts bucket

Remote state lives in an encrypted, versioned S3 bucket, with S3-native locking (`use_lockfile`). **Expected cost is under $1/month. Nothing has been deployed.**

Full setup, credentials guidance and costs are in **[infra/README.md](infra/README.md)**; the reasoning is in [ADR-001](adr/ADR-001-infrastructure-as-code.md).

```powershell
tofu -chdir=infra init -backend=false; tofu -chdir=infra validate; tofu -chdir=infra test   # offline, no AWS needed
```

## Kubernetes + Argo CD (GitOps)

**Security and runtime settings:**
- The `healthqueue` namespace enforces the Pod Security **restricted** level.
- The pod runs as uid 10001 with a read-only root filesystem, all capabilities dropped, and seccomp RuntimeDefault.
- **No service-account token** is mounted.
- Startup, readiness and liveness probes call `/health`.
- Resources: requests 50m CPU / 64Mi memory, limits 250m CPU / 128Mi memory.
- `replicas: 1` (see [ADR-002](adr/ADR-002-flask-in-memory-single-replica.md)).

**Validate:**
```powershell
kubectl kustomize k8s/base
kubectl kustomize k8s/base | docker run --rm -i ghcr.io/yannh/kubeconform:v0.7.0 -strict -summary -
kubectl apply -f k8s/argocd/healthqueue-app.yaml --dry-run=server     # needs Argo CD installed
kubectl diff -k k8s/base
```

**Test locally on kind** (the cluster name in this environment is `shipfast`):
```powershell
docker build -t healthqueue-api:ci .
kind load docker-image healthqueue-api:ci --name shipfast
kubectl apply -k k8s/overlays/local
kubectl -n healthqueue rollout status deployment/healthqueue-api
```

**Check the deployment, pods and service:**
```powershell
kubectl -n healthqueue get deploy,pods,svc,endpointslices -o wide
kubectl -n healthqueue describe deployment healthqueue-api
kubectl -n healthqueue logs deploy/healthqueue-api
kubectl -n healthqueue get events --sort-by=.lastTimestamp
kubectl -n healthqueue port-forward svc/healthqueue-api 8080:80     # in a second window:
Invoke-RestMethod http://localhost:8080/health
```

**Enable Argo CD.** Do this only after the code is pushed and an image has been published:
```powershell
kubectl delete -k k8s/overlays/local                    # remove the local test first
kubectl apply -n argocd -f k8s/argocd/healthqueue-app.yaml
kubectl -n argocd get applications
kubectl -n argocd get application healthqueue-api -o jsonpath="{.status.sync.status} {.status.health.status} {.status.sync.revision}"
kubectl -n argocd describe application healthqueue-api
```

Expected result: `Synced Healthy <sha>`.

Argo CD auto-syncs `main` with prune and self-heal. "Approved" changes are PRs merged into the protected `main`. **To release** a build, change `newTag` in `k8s/base/kustomization.yaml` to `sha-<commit>` in a PR. **To roll back**, revert that PR.

**Argo CD web UI:**
```powershell
kubectl -n argocd port-forward svc/argocd-server 8081:443      # https://localhost:8081, user admin
$pw = kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}"
[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($pw))
```

**Clean up:** `kubectl delete -k k8s/overlays/local`, or `kubectl -n argocd delete application healthqueue-api`. The finalizer also deletes the app's resources.

## Observability & DORA metrics

- **[OBSERVABILITY.md](OBSERVABILITY.md):**
  - SLOs: availability 99.5%, successful requests 99.9%, latency 95% < 300 ms
  - 30-day error budgets with a worked synthetic example
  - burn-rate alerts and an on-call runbook
  - how metrics, logs and traces fit together
  - duplicate-booking prevention with `Idempotency-Key`
  - These are a **plan**; they aren't implemented in `app.py` yet.
- **[DORA_ANALYSIS.md](DORA_ANALYSIS.md):** `python dora_metrics.py` generates 90 days of **synthetic** deployments and calculates:
  - deployment frequency
  - lead time for changes
  - change failure rate
  - failed deployment recovery time

  The analysis compares them with the DORA 2024 benchmarks and recommends ways to reduce manual work.

## Validate everything locally

Requirements: Docker Desktop, OpenTofu ≥ 1.10, and kubectl. None of these commands touch AWS.

```powershell
pytest -q                                                       # API + DORA tests
docker build -t healthqueue-api:ci .                            # image builds
docker run --rm -v ${PWD}:/src:ro aquasec/trivy:0.70.0 config --ignorefile /src/.trivyignore /src/infra
tofu fmt -check -recursive infra
tofu -chdir=infra test                                          # mocked AWS provider
kubectl kustomize k8s/base | docker run --rm -i ghcr.io/yannh/kubeconform:v0.7.0 -strict -summary -
docker run --rm -v ${PWD}:/repo -w /repo rhysd/actionlint:1.7.7 -no-color   # workflow lint
```

## Limitations

- **Data:** stored in memory and lost on restart; single replica only ([ADR-002](adr/ADR-002-flask-in-memory-single-replica.md)).
- **Security gaps:**
  - No authentication or authorization.
  - No NetworkPolicy, and no admission check that only signed images may run.
- **Image tag:** `k8s/base` uses `:latest` until the first image is published. This is a time-limited exception in `.trivyignore`.
- **Not implemented yet:** the observability stack and idempotency (see the checklist in `OBSERVABILITY.md`).
- **DORA data:** synthetic. Real data should come from GitHub and Argo CD.
