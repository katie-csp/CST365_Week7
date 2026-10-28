# HealthQueue Troubleshooting

These are PowerShell commands unless noted otherwise. For production incidents, use the on-call runbook in [OBSERVABILITY.md](OBSERVABILITY.md#5-on-call-runbook).

## Local Python

| Problem | Cause | Fix |
|---|---|---|
| `Activate.ps1 cannot be loaded because running scripts is disabled` | PowerShell execution policy | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, or call `.\.venv\Scripts\python.exe` directly |
| `ModuleNotFoundError: flask` / `pytest` | venv not active, or dev requirements missing | `pip install -r requirements-dev.txt` inside the venv |
| `pytest` collects nothing or imports the wrong `app` | Running from another folder | Run `pytest` from the project root |
| Port 5000 already in use | Another app (or the container) is using it | `docker stop healthqueue`, or `Get-NetTCPConnection -LocalPort 5000` to find the process |
| No interactive debugger | Debug mode is off by default (for security) | `$env:FLASK_DEBUG = "1"; python app.py` (local only) |
| `413` response | Request body over 16 KB | Send a smaller body; `reason` is limited to 250 characters anyway |

## Docker

| Problem | Fix |
|---|---|
| `error during connect` / `docker: command not found` | Start Docker Desktop and wait for "Engine running" |
| `pip install` fails during build with a connection reset | Network drop; rebuild (`docker build --pull ...`) |
| Container is `unhealthy` | `docker logs healthqueue`; `docker inspect --format '{{json .State.Health}}' healthqueue` |
| `Bind for 0.0.0.0:5000 failed: port is already allocated` | `docker ps`, then stop the other container, or use `-p 5001:5000` |
| `pip: not found` inside the container | Intended: pip is removed from the final image |
| Trivy finds new HIGH/CRITICAL vulnerabilities | Update the base image digest (or merge the Dependabot PR) and rebuild with `--pull` |

## GitHub Actions

| Failing step | Fix |
|---|---|
| `Run pytest` | Run `pytest -v` locally and fix the failure |
| `OpenTofu fmt` | `tofu fmt -recursive infra`, then commit |
| `kubeconform` | `kubectl kustomize k8s/base` locally; fix the reported field |
| Trivy config scan | Fix the finding, or add a **justified, expiring** entry to `.trivyignore` (Kubernetes) or an inline `# trivy:ignore:<ID>` with a reason (OpenTofu) |
| Trivy image gate | A fixable HIGH/CRITICAL vulnerability: update the base image or packages |
| Trivy `TOOMANYREQUESTS` downloading its database | Temporary rate limit; re-run the job |
| `denied: permission_denied` pushing to GHCR | Package settings → Manage Actions access → give this repo **Write** |
| Cosign `getting signer: ... id-token` | `id-token: write` is missing, or the run came from a fork or PR (publishing only runs on `main`) |
| Provenance or SARIF steps skipped | The repo is private; these features need a public repo or a paid plan |

## OpenTofu / AWS

| Problem | Fix |
|---|---|
| `No valid credential sources found` | `aws sso login --profile healthqueue`; `$env:AWS_PROFILE = "healthqueue"`. Never put keys in the repo |
| `Backend initialization required` | `tofu -chdir=infra init -backend-config=backend.hcl` |
| `NoSuchBucket` during init | Run the bootstrap first and check the bucket name in `backend.hcl` |
| `Error acquiring the state lock` | Someone else is running OpenTofu; wait. If a crashed run left the lock: `tofu force-unlock <LOCK_ID>` (only when sure) |
| `AccessDenied` on state | Attach the `healthqueue-tofu-state-access` IAM policy to your role |
| `BucketAlreadyExists` | Bucket names are global; the names include the account ID, so check the account and region |
| Can't destroy the state bucket | Intended (`prevent_destroy`); see infra/README "Tear down" |
| Unexpected AWS charges | Check for a NAT gateway or interface endpoints added outside this config; set a Budgets alert |

## Kubernetes

| Symptom (`kubectl -n healthqueue get pods`) | Cause | Fix |
|---|---|---|
| `ErrImageNeverPull` (local overlay) | Image not loaded into kind | `kind load docker-image healthqueue-api:ci --name shipfast` |
| `ImagePullBackOff` (base) | Image not published yet, wrong tag, or private GHCR package | Check `newTag`; make the package public |
| `CrashLoopBackOff` | App fails at start | `kubectl -n healthqueue logs deploy/healthqueue-api --previous` |
| `OOMKilled` | Over the 128Mi limit | Raise `limits.memory` in Git (PR) |
| `forbidden: violates PodSecurity "restricted"` | A manifest change removed a security setting | Restore the `securityContext` settings |
| A single `Startup probe failed: connection refused` event | Gunicorn takes ~4-6 s to start | Normal; only a problem if restarts increase |
| `namespaces "healthqueue" not found` on `--dry-run=server` | A dry-run doesn't create the namespace | Expected; use kubeconform, or apply for real |
| Wrong cluster | Current context is a different cluster | `kubectl config get-contexts`; `kubectl config use-context <name>` |
| `kubectl top` doesn't work | metrics-server isn't installed | Install metrics-server, or use `kubectl describe pod` |

## Argo CD

| Status | Fix |
|---|---|
| `ComparisonError: ... path does not exist` | Push `k8s/base` to `main` |
| `repository not accessible` | The repo must be public, or add repo credentials in Argo CD |
| `application destination ... is not permitted in project` | Destination must be the `healthqueue` namespace on `https://kubernetes.default.svc` |
| `resource ... is not permitted in project` | Add the kind to `namespaceResourceWhitelist` in the AppProject (via PR) |
| `OutOfSync` that keeps reverting | Self-heal is undoing a manual `kubectl` change; make the change in Git |
| Synced, but the new image isn't deployed | `latest` doesn't change in Git; use `sha-<commit>` tags |
| App stuck deleting | Finalizer waiting on resources: `kubectl -n argocd describe application healthqueue-api` |
