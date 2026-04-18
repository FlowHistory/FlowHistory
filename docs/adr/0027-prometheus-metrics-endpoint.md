# ADR 0027: Prometheus Metrics Endpoint

**Status:** Proposed
**Date:** 2026-04-16
**Author:** Leonardo Merza

## Context

### Background

FlowHistory has no observability surface today beyond a liveness-only `/health/` endpoint that returns `{"status": "ok"}`. Operators learn about backup failures only by visiting the dashboard or receiving a notification (ADR 0022). There is no way to alert on backup staleness, failure rates, HTTP errors, or retention activity from an existing Prometheus/Grafana stack.

ADR 0001 listed monitoring and Grafana integration as future work. This ADR acts on that intent by exposing a `/metrics` endpoint compatible with Prometheus scraping.

### Current State

- Three processes share one container: gunicorn (2 workers), `runapscheduler`, `runwatcher`.
- SQLite holds the authoritative state for every domain object worth monitoring: `NodeRedConfig.last_successful_backup`, `NodeRedConfig.last_backup_error`, `BackupRecord.status`, `BackupRecord.file_size`, `RestoreRecord.status`.
- Authentication is optional and session-based (`SimpleAuthMiddleware`). `/login/`, `/health/`, `/static/`, and `/metrics` are exempt from auth via a hardcoded path tuple.
- No Prometheus client or metrics instrumentation exists in the codebase.

### Requirements

- Expose application and domain metrics in Prometheus text format at a well-known path.
- Work across all three processes without adding inter-process coordination complexity.
- Respect the project's lightweight conventions (simple `JsonResponse` views, minimal middleware).
- Keep label cardinality bounded — no labels that grow unbounded (filenames, error strings).
- Allow operators to disable the endpoint entirely via env var.

### Constraints

- Single container, multi-process deployment.
- Django 6.0.3 on Python 3.13.
- Auth is optional and must remain so — the metrics endpoint must be scrapable whether or not `REQUIRE_AUTH=true`.
- No new long-running processes if avoidable.

## Options Considered

### 1. Prometheus client library

#### Option A: `prometheus-client` (manual instrumentation)

**Description:** Use the low-level Prometheus Python client directly. Hand-write a `/metrics` view and instrument code paths manually.

**Pros:**
- Minimal footprint and dependencies.
- Full control over metric names and labels.
- No middleware additions to the request path.

**Cons:**
- No free HTTP request/latency/status metrics — every metric must be authored.
- No free ORM query instrumentation.
- More code to maintain for equivalent coverage.

#### Option B: `django-prometheus` (chosen)

**Description:** Add `django-prometheus`, which provides middleware for automatic HTTP metrics, ORM instrumentation, and a drop-in `/metrics` URL include. Custom Gauges layered on top via `prometheus-client`.

**Pros:**
- Instant coverage of HTTP request count, latency histogram, and status codes.
- Drop-in URL include — no hand-written export view.
- Still allows custom metrics for domain data.

**Cons:**
- Adds middleware to every request.
- One more dependency to keep compatible with Django's release cadence.
- ORM query counters only emit if the DB `ENGINE` is switched to `django_prometheus.db.backends.sqlite3`. This ADR keeps the stock `django.db.backends.sqlite3` engine, so only HTTP auto-metrics are emitted — domain/DB state is captured separately by the custom collector below.

#### Option C: `django-prometheus` + `prometheus-client` multiproc

**Description:** Option B plus manual multiproc instrumentation in the scheduler and watcher for real-time job timings.

**Pros:**
- Captures scheduler job durations and watcher event rates.

**Cons:**
- Requires `PROMETHEUS_MULTIPROC_DIR` on a shared tmpfs with cleanup in `entrypoint.sh`.
- Gauge types need multiproc mode selection (`livesum`/`max`/`min`).
- Most of that detail is also recoverable from the DB at scrape time (see architecture section).

### 2. Endpoint authentication

#### Option A: Public (chosen)

**Description:** Expose `/metrics` without auth, following the same exemption pattern as `/health/`. Rely on network-layer controls (Docker network isolation, reverse proxy) to restrict access.

**Pros:**
- Zero scrape configuration on the Prometheus side.
- Matches the existing `/health/` precedent from ADR 0003.
- No secret to rotate.

**Cons:**
- Anyone on the network can enumerate instance slugs and operational data.
- Must discipline labels to avoid leaking filenames or error text.

#### Option B: Bearer token

**Description:** Require `Authorization: Bearer <METRICS_TOKEN>`. Prometheus reads the token via `bearer_token_file`.

**Pros:**
- Transport-agnostic auth.
- Matches the env-var-for-secrets convention (ADR 0021).

**Cons:**
- Extra config on the Prometheus side.
- Token rotation is manual.

#### Option C: Session auth (reuse `SimpleAuthMiddleware`)

**Description:** Do not exempt `/metrics` from auth.

**Pros:**
- No new auth code.

**Cons:**
- Prometheus scrapers do not do form login — effectively forces `REQUIRE_AUTH=false` to scrape, defeating the purpose.
- Not a real option in practice.

### 3. Multi-process architecture

#### Option A: Single endpoint in gunicorn, DB-polled domain metrics (chosen)

**Description:** `/metrics` served by gunicorn only. `django-prometheus` handles HTTP auto-metrics (ORM metrics are not emitted because this ADR keeps the stock SQLite engine). A custom `Collector` queries the DB on each scrape to emit per-instance domain Gauges (backup counts, last-run age, total size, error state). Scheduler and watcher are not instrumented — their state lives in the DB already.

**Pros:**
- No multiproc coordination.
- Single scrape job.
- Collector and dashboard share the same aggregation pattern (see `backup/views/pages.py`).

**Cons:**
- No realtime scheduler job duration histograms.
- No watcher event-rate counters.

#### Option B: Two endpoints — gunicorn plus sidecar exporter

**Description:** `django-prometheus` on gunicorn for HTTP/ORM. A fourth process (new `runmetrics` management command) serves DB-polled domain metrics on a different port.

**Pros:**
- Clean separation of web and domain metrics.

**Cons:**
- Four processes in one container.
- Two scrape jobs to configure.
- No realtime job timing either — the gain over Option A is marginal.

#### Option C: Full multiproc instrumentation

**Description:** `django-prometheus` on gunicorn plus `prometheus-client` multiproc mode shared with scheduler and watcher. Instrument `_scheduled_backup`, `_scheduled_retention`, and watcher debounce handlers with Histograms/Counters.

**Pros:**
- Captures everything, including realtime job durations and watcher event rates.

**Cons:**
- Adds `PROMETHEUS_MULTIPROC_DIR` setup and cleanup in `entrypoint.sh`.
- Adds multiproc mode selection for every Gauge.
- Disproportionate complexity for a single-container home-lab app.

## Decision

**Chosen combination:** Option 1B (`django-prometheus`) + Option 2A (public endpoint) + Option 3A (single endpoint, DB-polled domain metrics).

**Rationale:**

- `django-prometheus` gives HTTP and ORM instrumentation for free — the exact data a Prometheus user expects by default — without hand-writing an export view.
- Public access matches the `/health/` precedent, keeps Prometheus scrape config trivial, and is appropriate for the project's LAN-only deployment. Network-layer controls (Docker network, reverse proxy) are the right place to restrict access.
- Serving a single endpoint from gunicorn and letting a custom `Collector` read the DB at scrape time avoids multi-process coordination entirely. The DB is already the source of truth for every domain metric worth alerting on; polling it on each scrape (default 15s) is cheap because the aggregations are small indexed counts/sums.
- Realtime scheduler and watcher timing metrics are explicit non-goals for this iteration. A follow-up ADR can add multiproc instrumentation later if those signals become necessary.

## Consequences

### Positive

- Operators can scrape `/metrics` from existing Prometheus infrastructure with zero auth configuration.
- Free HTTP latency, status, and request-count histograms across all views.
- Per-instance domain metrics (backup counts, last-successful-backup age, total backup bytes, error state) enable staleness and failure alerts.
- Collector and dashboard ORM queries share the same shape, so they stay consistent.

### Negative

- `/metrics` is publicly readable inside the Docker network — operators must ensure it isn't exposed to untrusted networks.
- `django-prometheus` middleware runs on every request. Overhead is small but non-zero.
- No realtime scheduler job duration or watcher event rate metrics until a follow-up ADR.
- HTTP auto-metrics (`django_http_*`) are per-worker: gunicorn runs 2 workers without `PROMETHEUS_MULTIPROC_DIR`, so each scrape hits one worker and counters/histograms can appear to reset between scrapes. Custom domain metrics (`flowhistory_*`) are unaffected because the collector reads the shared SQLite DB. Operators who need smooth HTTP metrics can either run a single gunicorn worker or graduate to multiproc mode in a follow-up.

### Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `django-prometheus` version incompatible with Django 6.0.3 | Medium | Medium | Verify at build time; fall back to a hand-written `prometheus-client` export view (~30 lines) if needed. |
| DB aggregation queries slow at scrape time | Low | Low | Queries are indexed count/sum aggregations over a small table. If latency grows, add a short-TTL `functools.lru_cache` in the collector. |
| Label cardinality explosion | Low | Medium | Hard rule: only `instance` (slug) and `status` (enum) labels. Never filenames or error text. Enforced in tests. |
| Endpoint exposed to untrusted networks | Low | Medium | Document in README that `/metrics` is public; recommend reverse-proxy restriction. Provide `METRICS_ENABLED=false` opt-out. |

## Implementation Plan

- [ ] Add `django-prometheus` to `pyproject.toml`; verify compatibility with Django 6.0.3 inside the dev container.
- [ ] Update `config/settings.py`: add `django_prometheus` to `INSTALLED_APPS`, wrap `MIDDLEWARE` with before/after middleware, add `METRICS_ENABLED` env flag.
- [ ] Add `/metrics` route in `backup/urls.py` (guarded by `METRICS_ENABLED`) using `include('django_prometheus.urls')`.
- [ ] Extend `EXEMPT_PATHS` in `backup/middleware/simple_auth.py` to include `/metrics`.
- [ ] Create `backup/metrics.py` with `FlowHistoryCollector` implementing `collect()` for `flowhistory_backups`, `flowhistory_backup_bytes`, `flowhistory_last_successful_backup_timestamp_seconds`, `flowhistory_instance_enabled`, `flowhistory_instance_has_error`, `flowhistory_restores`, `flowhistory_pinned_backups`. Use plain names (no `_total` suffix) because these are Gauges — `_total` is reserved for monotonic Counters in Prometheus conventions.
- [ ] Register the collector in `backup/apps.py` `ready()` under the `METRICS_ENABLED` guard.
- [ ] Add `backup/tests/test_metrics.py` covering: auth bypass with `REQUIRE_AUTH=true`, correct content-type, metric presence, DB-state reflection, `METRICS_ENABLED=false` returns 404, label cardinality bounds.
- [ ] Update `README.md`: add `/metrics` to endpoint docs, add `METRICS_ENABLED` to env-var table, include Prometheus scrape-config example.
- [ ] User verifies end-to-end against real Prometheus scraper; only then update status to **Implemented** and bump the version per the ADR workflow.

## Related ADRs

- [ADR 0001](./0001-nodered-backup-architecture.md) — originally flagged monitoring/Grafana as future work.
- [ADR 0003](./0003-dockerization-and-basic-auth.md) — established the public `/health/` endpoint pattern that `/metrics` follows.
- [ADR 0013](./0013-multi-instance-support.md) — the multi-instance model drives the `instance` label on every custom metric.
- [ADR 0021](./0021-instance-credential-storage.md) — env-var convention reused for `METRICS_ENABLED`.
- [ADR 0022](./0022-notification-system.md) — event-driven dispatch noted as a rejected alternative for metric emission; domain metrics come from DB state instead.

## References

- django-prometheus: https://github.com/django-commons/django-prometheus
- prometheus_client custom collectors: https://prometheus.github.io/client_python/collector/custom/
- Prometheus exposition format: https://prometheus.io/docs/instrumenting/exposition_formats/
