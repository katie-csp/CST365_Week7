# HealthQueue Observability Plan

> **All traffic figures, log lines, traces and incidents in this document are SYNTHETIC examples.** They were invented for planning and teaching. HealthQueue handles synthetic data only, and no real patient information (PHI) may ever appear in metrics, logs or traces.

**Status:** plan. The current app has `/health` and Kubernetes probes. The metrics, structured logging, tracing and idempotency described below are **not implemented yet**. See [Implementation checklist](#8-implementation-checklist).

---

## 1. SLIs and SLOs

All SLOs use a **rolling 30-day window** (30 × 24 × 60 = **43,200 minutes**).

| # | SLI (what we measure) | Good event | Valid events | SLO |
|---|---|---|---|---|
| 1 | **Availability** | A 1-minute interval in which a synthetic probe gets `200` from `/health` **and** `GET /appointments` | Every minute in the window | **99.5%** of minutes |
| 2 | **Successful requests** | API response that is **not** a 5xx | All API requests except `/health` probes. 4xx counts as valid but **not** failed, because client errors aren't the server's fault | **99.9%** of requests |
| 3 | **Latency** | Request served in **< 300 ms** (fast) or **< 1,000 ms** (acceptable) | All non-5xx API requests | **95%** < 300 ms **and** 99% < 1,000 ms |

Why both 1 and 2: `/health` only proves the process is up. The availability probe also calls `GET /appointments`, and SLO 2 measures what real (synthetic) users actually experience.

## 2. 30-day error budgets

Error budget = (1 − SLO) × valid events in the window.

| SLO | Calculation | Budget per 30 days |
|---|---|---|
| Availability 99.5% | 0.005 × 43,200 min | **216 minutes** (3 h 36 min) of failed probe minutes |
| Successful requests 99.9% | 0.001 × requests | At **30 req/min**: 30 × 43,200 = **1,296,000 requests**, so 0.001 × 1,296,000 = **1,296 failed requests** |
| Latency 95% < 300 ms | 0.05 × requests | 0.05 × 1,296,000 = **64,800 slow requests** |
| Latency 99% < 1 s | 0.01 × requests | 0.01 × 1,296,000 = **12,960 very slow requests** |

### Numerical example (SYNTHETIC)

**Assumed traffic: 30 requests/minute, steady. SYNTHETIC.**

| SYNTHETIC event | Availability budget used | Request budget used |
|---|---|---|
| **INC-SYN-001:** full outage for **30 min** (pod crash-looping) | 30 of 216 min = **13.9%** | 30 min × 30 req = **900** failed requests = 900 ÷ 1,296 = **69.4%** |
| **INC-SYN-002:** **10 min** of partial errors, 25% of requests return 503; probes still pass | 0 min (every probe minute passed) | 10 × 30 × 0.25 = **75** failed requests = **5.8%** |
| **Total** | **30 min = 13.9%** used, **186 min (86.1%)** left | **975 = 75.2%** used, **321 failures (24.8%)** left |

**What this shows:**
- **The request SLO is much stricter.** The same 30-minute outage used 13.9% of the availability budget but 69.4% of the request budget. With 24.8% left, a further full outage of more than ~10.7 minutes (321 ÷ 30) would exhaust it.
- **Burn rate** is the observed error rate ÷ the allowed error rate:
  - INC-SYN-001: 100% ÷ 0.1% = **1,000×**, which would use the whole monthly budget in 43,200 ÷ 1,000 ≈ **43 minutes**.
  - INC-SYN-002: 25% ÷ 0.1% = **250×**.
- **Latency example (SYNTHETIC):** one day with 8% of requests over 300 ms gives 43,200 requests × 0.08 = **3,456** slow requests. That is 3,456 ÷ 64,800 = **5.3%** of the 30-day latency budget, a burn rate of 8% ÷ 5% = **1.6×**.

### Error-budget policy

| Budget remaining | Action |
|---|---|
| > 50% | Normal releases |
| 25–50% | Releases need a rollback plan; prioritise reliability bugs |
| < 25% (as in the example above) | **Feature freeze.** Only reliability fixes and security patches until the budget recovers |
| Exhausted | Freeze, plus a mandatory postmortem and an action item reviewed before the next release |

## 3. Metrics, logs and traces

| Signal | Answers | Example HealthQueue use |
|---|---|---|
| **Metrics** | *Is something wrong, and how badly?* | Error-rate and latency alerts, budget burn, saturation |
| **Structured logs** | *What exactly happened to these requests?* | The error type, which validation failed, retries and idempotency replays |
| **Distributed traces** | *Where did the time go, and which component failed?* | Slow spans (storage, validation), a failing dependency, retries end to end |

The three are linked by a shared **`request_id` / `trace_id`**. That lets you go from an alert to the related logs to one exact trace.

### 3.1 Metrics (Prometheus format, via `prometheus_flask_exporter` or OpenTelemetry)

| Metric | Type | Labels | Used for |
|---|---|---|---|
| `http_requests_total` | counter | `method`, `route`, `status` | SLO 2, traffic, 4xx/5xx rates |
| `http_request_duration_seconds` | histogram, buckets incl. **0.3** and **1.0** | `method`, `route` | SLO 3 (bucket `le="0.3"` gives the "fast" fraction) |
| `probe_success` | gauge (blackbox exporter) | `target` | SLO 1 |
| `appointments_created_total` | counter | — | Business activity |
| `appointment_conflicts_total` | counter | `reason` (`slot_taken`, `idempotency_mismatch`) | Duplicate prevention |
| `idempotent_replays_total` | counter | — | Shows client retries are working safely |
| `kube_pod_container_status_restarts_total`, OOMKilled, CPU/memory vs. limits | from kube-state-metrics / cAdvisor | `namespace="healthqueue"` | Crashes, saturation |

**Label rules:**
- `route` is the **template** (`/appointments/<id>`), never the real path. This keeps the number of distinct time series small, and IDs never end up in metrics.
- Labels **never** contain names, reasons, dates or idempotency keys.

**Example SLI queries (PromQL):**

```promql
# SLO 2 - error ratio over 1h
sum(rate(http_requests_total{route!="/health",status=~"5.."}[1h]))
  / sum(rate(http_requests_total{route!="/health"}[1h]))

# SLO 3 - fraction of requests faster than 300 ms over 1h
sum(rate(http_request_duration_seconds_bucket{route!="/health",le="0.3"}[1h]))
  / sum(rate(http_request_duration_seconds_count{route!="/health"}[1h]))
```

### 3.2 Structured logs (JSON, one line per event, to stdout)

**Example (SYNTHETIC):**

```json
{"ts":"2030-01-15T09:30:02.114Z","level":"ERROR","service":"healthqueue-api","env":"dev",
 "event":"request_failed","request_id":"7f3c2a9e-SYNTHETIC","trace_id":"4bf92f3577b34da6a3ce929d0e0e4736",
 "method":"POST","route":"/appointments","status":503,"duration_ms":2004,
 "error_code":"STORAGE_TIMEOUT","idempotency_key_sha256":"9b1e…","retry_attempt":2,
 "appointment_id":null,"synthetic":true}
```

- **Allowlist logging:** only the fields above are logged. Never log request bodies, `patient_name`, `reason`, `Authorization` headers, cookies, tokens or raw idempotency keys (log a SHA-256 hash instead).
- A redaction filter drops any field that isn't on the allowlist. A unit test asserts that a synthetic patient name never appears in log output.
- Validation errors log the **field name and rule** (`"field":"date","rule":"format"`), never the submitted value.
- Every request gets a `request_id`, returned to the client in the `X-Request-ID` header, so support can find the logs without needing any personal details.

**How logs help:**
- Filter `level=ERROR route=/appointments` and group by `error_code` to see *which* failure is happening.
- Search for a `request_id` from a user's report to find exactly what happened to that request.

### 3.3 Distributed traces (OpenTelemetry → Jaeger/Tempo/OTLP backend)

**Example trace (SYNTHETIC)**, a slow `POST /appointments` (total 1,240 ms):

```
POST /appointments                         1,240 ms   trace_id 4bf92f…   synthetic=true
├── validate_appointment                       2 ms
├── idempotency.lookup                     1,180 ms   ◄── the bottleneck (lock wait)
├── storage.insert_appointment                41 ms
└── serialize_response                         1 ms
```

- **Span attributes:** route, status, `appointment.id`, `idempotency.replayed=true/false`, `retry.attempt`. **No PHI:** never patient names or reasons. The OpenTelemetry Flask instrumentation must be configured not to capture request bodies or headers.
- **Context propagation:** the W3C `traceparent` header links client, API and future database spans into one trace.
- **Sampling:** keep 100% of error traces and slow traces (> 1 s), and 10% of the rest (tail sampling in the OpenTelemetry Collector).
- **How traces help:** a metric shows that p95 rose; the trace shows *which step* (e.g. `idempotency.lookup`) caused it.

### 3.4 Investigation flow

1. **Alert fires** (metric): e.g. a request-SLO burn rate of 14.4×.
2. **Dashboard** (metrics): which route, which status code, when it started, and whether it lines up with a deploy (Argo CD sync).
3. **Logs:** filter that route and time window with `level>=ERROR`, then group by `error_code`.
4. **Traces:** open an example `trace_id` from those logs and find the slow or failing span.
5. **Fix or mitigate** (runbook below), then confirm the burn rate returns below 1×.

## 4. Alert thresholds

These are multi-window burn-rate alerts. An alert fires only if **both** the long and the short window are over the threshold, which avoids alerts from brief spikes and clears quickly after recovery.

### SLO alerts

| Alert | Condition | Budget used if it continues | Severity |
|---|---|---|---|
| `HQRequestBudgetFastBurn` | burn > **14.4×** (error ratio > **1.44%**) over **1h AND 5m** | 2% of the budget in 1 h | **Page** |
| `HQRequestBudgetSlowBurn` | burn > **6×** (error ratio > **0.6%**) over **6h AND 30m** | 5% in 6 h | **Page** |
| `HQRequestBudgetTicket` | burn > **1×** (error ratio > **0.1%**) over **3d AND 6h** | 10% in 3 days | Ticket |
| `HQLatencyBudgetFastBurn` | fraction > 300 ms is above **14.4 × 5% = 72%** over 1h AND 5m | 2% in 1 h | Page |
| `HQLatencyBudgetSlowBurn` | fraction > 300 ms is above **6 × 5% = 30%** over 6h AND 30m | 5% in 6 h | Ticket |
| `HQAvailabilityProbeFailing` | `probe_success == 0` for **5 min** | 5 of 216 min | **Page** |

**Check:** the 14.4× threshold over 1 h uses 14.4 × 1 ÷ 720 h = **2%** of a 30-day budget, and 6× over 6 h uses 6 × 6 ÷ 720 = **5%**. So the table is internally consistent.

**Low-traffic guard:** at 30 req/min (SYNTHETIC), a 5-minute window holds only ~150 requests, so 3 errors would cross 1.44%. Every burn-rate rule also requires **≥ 100 requests** in the short window.

### Symptom alerts

| Alert | Condition | Severity |
|---|---|---|
| `HQPodCrashLooping` | restarts > **3 in 15 min** | Page |
| `HQOOMKilled` | any container terminated with `OOMKilled` | Ticket (page if repeated) |
| `HQNotReady` | Deployment available replicas < 1 for **5 min** | Page |
| `HQMemoryNearLimit` | working set > **85%** of the 128Mi limit for 15 min | Ticket |
| `HQCPUThrottling` | throttled CPU periods > **25%** for 15 min | Ticket |
| `HQNoTraffic` | `absent(http_requests_total)` or 0 req/s for **15 min** when traffic is expected | Ticket |
| `HQDuplicateConflictsSpike` | `appointment_conflicts_total{reason="idempotency_mismatch"}` > **10 in 10 min** | Ticket (possible client bug) |
| `HQArgoOutOfSync` | the Argo CD app is not `Synced`/`Healthy` for **15 min** | Ticket |

**Rules:** every alert links to a runbook section, and alerts never include request contents.

## 5. On-call runbook

### 5.0 First 5 minutes (any page)
1. **Acknowledge** the page. Open an incident channel and name an incident lead.
2. **Assess impact.** Check the SLO dashboard: what is the error rate, which routes are affected, and how much budget has been used.
3. **Check recent changes.** Was there an Argo CD sync or image change in the last hour?
   ```powershell
   kubectl -n argocd get application healthqueue-api -o jsonpath="{.status.sync.revision} {.status.operationState.finishedAt}"
   kubectl -n healthqueue rollout history deployment/healthqueue-api
   ```
4. **Mitigate first, investigate second.**

### 5.1 `HQRequestBudgetFastBurn` / `SlowBurn` (5xx errors)
```powershell
kubectl -n healthqueue get deploy,pods -o wide
kubectl -n healthqueue logs deploy/healthqueue-api --since=30m | Select-String '"level":"ERROR"' | Select-Object -Last 20
kubectl -n healthqueue get events --sort-by=.lastTimestamp | Select-Object -Last 15
```
- **Started right after a deploy:** roll back **through Git**. Revert the commit (or set `newTag` back) in a PR and merge it, and Argo CD redeploys. Don't use `kubectl rollout undo`, because Argo CD's self-heal would undo it.
- **Only one `error_code`:** open a trace for it (section 3.4) and fix forward or turn off the feature.
- **Pod unhealthy but no recent deploy:** `kubectl -n healthqueue delete pod -l app.kubernetes.io/name=healthqueue-api` restarts it. ⚠ **Warning:** the in-memory data is lost. Record this in the incident notes.

### 5.2 `HQLatencyBudget*`
- Check CPU throttling and memory: `kubectl -n healthqueue describe pod -l app.kubernetes.io/name=healthqueue-api` (look at limits and the last state).
- Open slow traces (> 1 s) and find the slowest span.
- If CPU is throttled, raise `limits.cpu` in Git through a PR. There's **no horizontal scaling** until storage moves to a database (`replicas: 1`).

### 5.3 `HQAvailabilityProbeFailing` / `HQNotReady` / `HQPodCrashLooping`
```powershell
kubectl -n healthqueue get pods
kubectl -n healthqueue describe pod -l app.kubernetes.io/name=healthqueue-api   # look for: ImagePullBackOff, OOMKilled, probe failures
kubectl -n healthqueue logs deploy/healthqueue-api --previous                  # logs from the crashed container
```

| Symptom | Likely cause | Action |
|---|---|---|
| `ImagePullBackOff` | Wrong tag, or the GHCR package is private | Fix `newTag` in Git; make the package public |
| `OOMKilled` | Memory over 128Mi (in-memory data growth) | Raise the limit through a PR, then plan the move to a database |
| Startup probe failing repeatedly | App fails at boot | Check `--previous` logs; roll back through Git |
| `CreateContainerConfigError` / Pod Security error | A manifest change broke the restricted settings | Revert the manifest PR |

### 5.4 `HQArgoOutOfSync`
```powershell
kubectl -n argocd describe application healthqueue-api   # read Conditions
```
Common causes: the repo is unreachable, the path was renamed, a resource kind isn't allowed by the AppProject, or a manual `kubectl` edit is being reverted by self-heal.

### 5.5 Close-out
1. Confirm the burn rate has been below 1× for 30 minutes, then resolve the incident.
2. Write a **blameless postmortem within 2 working days**: timeline, impact (budget used), root cause, and action items with owners. Use the synthetic format below.
3. Update this runbook if any step was missing or wrong.

**Postmortem header example (SYNTHETIC):**

> **INC-SYN-001 (SYNTHETIC)**: 30-minute outage, 2030-01-15 09:30–10:00 UTC.
> - **Impact:** 900 failed requests (SYNTHETIC traffic). 69.4% of the request budget and 13.9% of the availability budget used.
> - **Cause:** a new image exceeded the memory limit, then OOMKilled and crash-looped.
> - **Fix:** reverted `newTag` through a PR, and Argo CD redeployed within 3 min.
> - **Actions:** add a memory test to CI; add a load test.

## 6. Avoiding duplicate appointments during retries

**The problem:** a client sends `POST /appointments`, the server books the slot, but the response is lost (timeout or network drop). The client retries and **books twice**.

### Steps
1. **Require an `Idempotency-Key` header on `POST /appointments`.** The client generates one random UUIDv4 per *intended booking* and **reuses the same key on every retry** of that booking. A missing key returns `400`.
2. **Fingerprint the request.** The server computes SHA-256 of the canonical JSON body, so the same key with a different body can be detected.
3. **Atomic check-and-store.** Under a lock or a database transaction, the server looks up the key:
   - **New key:** mark it `in_progress`, create the appointment, then store `{key_hash, fingerprint, status_code, response_body, created_at}`.
   - **Same key and same fingerprint, completed:** **replay the stored response** (`201` with the same appointment `id`) and add the header `Idempotent-Replayed: true`. No new appointment is created.
   - **Same key, still `in_progress`:** return `409 Conflict` with `Retry-After: 1`.
   - **Same key, different fingerprint:** return `422 Unprocessable Entity` (the key was reused for a different booking).
4. **Expire keys after 24 hours** (TTL), which is longer than any client's retry window.
5. **Add a business uniqueness rule as a second safety net.** One active appointment per `(provider, date, time)` slot: a second booking returns `409 Conflict` with `error_code: SLOT_TAKEN`. With a database, this becomes a **unique index** so it holds even if two requests race.
6. **Make cancellation idempotent.** `DELETE` on an already-cancelled appointment returns `200` with the same cancelled record (the current app already behaves like this).
7. **Client retry policy:**
   - Retry only on network errors, `502`, `503`, `504` and `409` with `Retry-After`. **Never** retry other `4xx` errors.
   - Use exponential backoff with jitter (e.g. 0.5 s, 1 s, 2 s, ±20%), with **at most 3 retries**, always reusing the **same** `Idempotency-Key`.
8. **Make it observable:**
   - Count `idempotent_replays_total` and `appointment_conflicts_total`.
   - Log `idempotency_key_sha256` (never the raw key) and `retry_attempt`.
   - Set the trace attribute `idempotency.replayed`.
9. **Test it** with synthetic data:
   - Same key twice: 1 appointment, both responses identical.
   - Same key with a different body: 422.
   - Two concurrent requests with the same key: exactly 1 appointment.
   - Different keys for the same slot: 409.

**Current-app note:** storage is in memory with one Gunicorn worker. The idempotency store must therefore live in the same process for now, and move to the database (or Redis) together with the appointments, so it survives restarts and works with multiple replicas.

## 7. Protecting sensitive information

- **Synthetic data only**, enforced by policy, README warnings and test fixtures. Dashboards and alerts label the environment `synthetic=true`.
- **No PHI in telemetry:**
  - metric labels are an allowlist
  - logs go through an allowlist plus redaction filter
  - trace attributes are an allowlist, with body and header capture **disabled**
- **Hash identifiers** that could link to a person or session (idempotency keys, client IPs if ever needed) with SHA-256 before logging.
- **Access control:** dashboards and logs need SSO login, and only on-call engineers can query raw logs.
- **Retention:** metrics 30 days (enough for the SLO window), logs 14 days, traces 7 days.
- **Transport and storage:** OTLP and log shipping over TLS; storage backends encrypted at rest (same approach as the S3 settings in `infra/`).
- **Incident notes** never include real personal data. Use `request_id` and `trace_id` to refer to requests.
- **CI check (planned):** a test sends a request with the synthetic name `"Test Patient Alpha"` and fails if that string appears in captured logs or span attributes.

## 8. Implementation checklist

| Step | Change | Effort |
|---|---|---|
| 1 | JSON logging with allowlist/redaction, `request_id` and `X-Request-ID` in `app.py` | Small |
| 2 | `/metrics` endpoint (`prometheus_flask_exporter`) with route-template labels | Small |
| 3 | OpenTelemetry Flask instrumentation plus an OTLP exporter (no body/header capture) | Medium |
| 4 | `Idempotency-Key` handling plus `SLOT_TAKEN` uniqueness, with tests (section 6) | Medium |
| 5 | Prometheus, Grafana and a blackbox probe on the cluster; alert rules from section 4 | Medium |
| 6 | Move storage to a database, then remove the single-replica limit | Large |
