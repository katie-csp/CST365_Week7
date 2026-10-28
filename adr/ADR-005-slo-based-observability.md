# ADR-005: SLO-based observability and DORA measurement

- **Status:** Accepted (plan; implementation pending)
- **Date:** 2026-10-28

## Context
Alerting on raw CPU or individual errors produces noise. The team needs to know when *users* (synthetic) are affected and whether delivery is improving.

## Decision
- **Three SLOs**, all over a rolling 30-day window:
  - availability 99.5% (216 min of budget)
  - successful requests 99.9%
  - latency 95% < 300 ms
- **Multi-window burn-rate alerts:** page at 14.4× (1h/5m) and 6× (6h/30m); ticket at 1× (3d/6h).
- **Telemetry:** Prometheus metrics, JSON logs with an **allowlist** of fields (no PHI), and OpenTelemetry traces. They are linked by `request_id` and `trace_id`.
- **Duplicate prevention:** `Idempotency-Key` on `POST /appointments`, plus a slot-uniqueness rule.
- **DORA metrics**, measured with `dora_metrics.py`. It uses synthetic data today and should be fed from GitHub and Argo CD later.

## Consequences
- ➕ Alerts are tied to user impact, the error-budget policy guides release decisions, and delivery is measurable.
- ➖ Requires a Prometheus/Grafana/OTel stack, and changes to `app.py` (see the checklist in `OBSERVABILITY.md`).
- ➖ At low traffic, burn-rate alerts need a minimum request count to avoid false pages.
