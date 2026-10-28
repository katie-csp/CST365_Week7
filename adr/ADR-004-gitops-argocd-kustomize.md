# ADR-004: GitOps deployment with Argo CD and Kustomize

- **Status:** Accepted
- **Date:** 2026-10-28

## Context
Deployments should be reviewable, repeatable and reversible, without anyone running `kubectl apply` by hand against shared clusters.

## Decision
- **Kustomize:**
  - `k8s/base` is what gets deployed (GHCR image).
  - `k8s/overlays/local` uses the locally built image on kind.
- **Pod security:**
  - The namespace enforces the Pod Security **restricted** level.
  - Non-root, read-only root filesystem, all capabilities dropped, seccomp RuntimeDefault, and no service-account token.
  - Startup, readiness and liveness probes on `/health`, plus CPU and memory requests and limits.
- **Argo CD:**
  - A dedicated **AppProject** limited to this repo, the `healthqueue` namespace, and four resource kinds.
  - The Application **auto-syncs `main`** with `prune` and `selfHeal`.
  - "Approved" changes = PRs merged into a protected `main`.
- **Rollback** = a `git revert` merged to `main`. `kubectl rollout undo` would be reverted by self-heal.

## Consequences
- ➕ Git history is the deployment log, and manual drift is undone automatically.
- ➖ The image is currently `:latest` because nothing is published yet. This is a temporary Trivy exception, **expiring 2026-12-31**. It must be pinned to `sha-<commit>`, ideally through an automated PR from CI.
- ➖ No NetworkPolicy or admission check of Cosign signatures yet. Recommended next steps: a default-deny NetworkPolicy, and Kyverno/Sigstore policy-controller to allow only signed images.
