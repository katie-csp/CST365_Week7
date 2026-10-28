# ADR-002: Flask API with in-memory storage and a single replica

- **Status:** Accepted (to be revisited before any production use)
- **Date:** 2026-10-28

## Context
The capstone needs a small, explainable appointment API that can be tested, containerized and deployed end to end. It only ever handles synthetic data.

## Decision
- **Flask 3** with an **app factory** (`create_app()`), so each test gets a fresh app.
- Appointments are stored **in memory** (a Python dict).
- In the container, it runs on **Gunicorn with 1 worker and 4 threads**. Kubernetes runs `replicas: 1`.
- Every error is returned as **JSON**. Request bodies are limited to **16 KB**, and the Flask debugger is **off** unless `FLASK_DEBUG=1`.

## Consequences
- ➕ No database to install. Tests run in about 1 second, and the code is easy to read.
- ➖ **Data is lost on every restart or redeploy.**
- ➖ **It can't scale horizontally.** Extra workers or replicas would each have separate data, so availability depends on one pod.
- ➖ The idempotency design (`OBSERVABILITY.md` §6) needs shared storage before it can work across replicas.

## Revisit when
Persistence or high availability is needed. The next step is PostgreSQL (e.g. RDS in the `infra/` VPC) with a unique index on (provider, date, time), then raising the replica count.
