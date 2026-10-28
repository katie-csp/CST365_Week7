# ADR-003: Hardened container image with keyless signing

- **Status:** Accepted
- **Date:** 2026-10-28

## Context
The image must be small, run without root, contain no secrets or patient data, and be verifiable by anyone who deploys it. Long-lived signing keys and registry passwords are a liability for a student project.

## Decision
- **Multi-stage Dockerfile** on `python:3.11-slim`, **pinned by digest**.
- The final image runs as **non-root uid 10001**, with **pip, setuptools and wheel removed**. An allowlist `.dockerignore` means only `app.py` and `requirements.txt` can be copied in.
- **GitHub Actions** pipeline:
  - pytest and IaC/manifest validation
  - **Trivy** gate on fixable HIGH/CRITICAL vulnerabilities
  - **Syft** SPDX SBOM
  - push to **GHCR** with the short-lived `GITHUB_TOKEN`
  - **Cosign keyless signing** and an SBOM attestation via **GitHub OIDC**
  - SLSA provenance via `actions/attest`
- **All actions are pinned to commit SHAs**, and Dependabot (with a cooldown) keeps them current.

## Alternatives rejected
- **Key-based Cosign signing:** a key must be stored and rotated.
- **Docker Hub with a password secret:** a long-lived credential.
- **Scanning a rebuilt image instead of the pushed one:** what you scan might differ from what you ship. The pipeline pushes the exact scanned tarball instead.

## Consequences
- ➕ No stored secrets. Signatures and provenance are publicly verifiable. Meets SLSA Build L2.
- ➖ The repo and workflow names are recorded in the public Sigstore Rekor log.
- ➖ Provenance and code scanning need a public repo (or paid GitHub plans).
- ➖ New CVEs can fail a previously green build. This is intended.
