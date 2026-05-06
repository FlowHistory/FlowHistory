# ADR 0029: Demo Mode

**Status:** Proposed
**Date:** 2026-05-05
**Author:** Leonardo Merza

## Context

### Background

There is no way today to host a public, "click around" instance of FlowHistory for evaluation. Anyone wanting to try the app must clone the repo, configure at least one Node-RED instance via env vars, and run the container. Operators who would like to share a screenshot-quality demo URL — for example linked from the README — have no safe option, because every view that mutates state currently writes to disk, deploys flows to a real Node-RED instance, sends real notification webhooks, or runs real scheduled jobs.

### Current State

- Authentication is optional (`REQUIRE_AUTH`) and gated by `SimpleAuthMiddleware` (ADR 0003, ADR 0026).
- All write surfaces are reachable through one of two channels:
  1. Unsafe HTTP methods (`POST`/`PUT`/`DELETE`) hitting view functions in `backup/views/api.py`, `backup/views/backups.py`, and `backup/views/pages.py`.
  2. Background processes started by `entrypoint.sh`: `runapscheduler` (per-instance scheduled backups + retention) and `runwatcher` (local file watching + remote API polling).
- API responses follow a `{"status": "success|error|...", "message": ...}` envelope. The frontend in `backup/static/backup/js/app.js` calls `showBanner(data.message)` whenever `data.status === 'error'`.
- The base template (`backup/templates/backup/base.html`) has no global mode banner today.

### Requirements

- A single env var (`DEMO_MODE`) puts the app into a safe, read-only state suitable for public exposure.
- All GET pages remain fully functional so the UI demos the feature surface.
- All write attempts (form posts, JSON API mutations) are intercepted with a clear "demo mode — not saved" message, without per-view code changes.
- No real Node-RED instance is contacted, no webhook is fired, no scheduled job runs.
- The user can see at every page that the deployment is in demo mode.
- Default off — production deployments are unaffected when the env var is unset.

### Constraints

- The user explicitly asked for the **least invasive** implementation. A per-service or per-view interception approach (originally surveyed at ~19 touch points) was rejected.
- The middleware order in `config/settings.py` already wraps Prometheus before/after middleware around the app. Any new middleware must respect that ordering.
- The container is multi-process (`gunicorn` + `runapscheduler` + `runwatcher`); shutting off background processes must not break the existing signal-trap in `entrypoint.sh`.

## Options Considered

### 1. Single edge-level write blocker (chosen)

**Description:** Add one new middleware (`DemoModeMiddleware`) that, when `DEMO_MODE=true`, lets every safe HTTP method (`GET`/`HEAD`/`OPTIONS`/`TRACE`) through unchanged and intercepts every unsafe method with a JSON envelope (`/api/` paths) or a `messages.warning(...)` plus redirect (HTML form posts). Skip `runapscheduler` and `runwatcher` startup in `entrypoint.sh`. Inject `demo_mode` into the template context and render a yellow banner under the navbar in `base.html`. Force `REQUIRE_AUTH=False` in `config/settings.py` when `DEMO_MODE=true` so the demo URL is reachable without a password.

**Pros:**
- One new file (~30-line middleware), one new test file, and five small edits — total ≈ 110 lines.
- Zero changes to any service in `backup/services/` or any view in `backup/views/`.
- Behaviour is centralised in one place: anyone reading the middleware understands the entire demo contract.
- Frontend gets the demo toast for free because the JSON envelope matches the existing `{"status": "error", "message": ...}` shape that `showBanner` already handles.
- Cannot be bypassed by a forgotten code path, because every state mutation in this app reaches the user through an HTTP method or a background loop, both of which this option intercepts.

**Cons:**
- Cannot demo "click-through" workflows where a created backup appears in the list — the POST is bounced before any DB write, so the dashboard does not pick up the new row.
- The "demo mode" message piggybacks on the `status: error` envelope, which is technically not an error. Acceptable: the user-facing toast text is what matters and the frontend treats this exactly like a friendly server-side rejection.

### 2. Ephemeral-container persistence

**Description:** Run the container with no host volume mounts, so the SQLite DB and backup tar files live entirely inside the container and vanish on restart. Add small `DEMO_MODE` guards to `restore_service.py` and `notification_service.py` only (the two services that talk to external systems). Skip `runapscheduler` and `runwatcher`. Banner as in Option 1.

**Pros:**
- Click-through workflows work: creating a backup writes a fake tar inside the container, the row appears in the list, labels and pins persist for the session.
- Container restart resets demo state — operators get a clean slate by recycling the container.

**Cons:**
- Touches two service files (still small, but no longer zero).
- Requires a separate `docker-compose.demo.yml` to remove volume mounts, plus a fixture-seed step in `entrypoint.sh`. More moving parts.
- Slightly higher risk of accidentally executing a side effect — for example, a future contributor adds a new write path that calls a third external service and forgets to guard it.

### 3. Per-view and per-service `DEMO_MODE` checks

**Description:** Add `if settings.DEMO_MODE: return mock_response(...)` to every POST view in `backup/views/api.py` (10 views) and to every public method in `backup/services/{backup,restore,notification,retention}_service.py` (5 entry points). Plus the entrypoint and banner edits.

**Pros:**
- Most flexibility — each action can choose how to fake itself (some succeed visually, some return a tailored toast).
- Per-action control might allow some workflows to "appear to work" without going as far as Option 2.

**Cons:**
- ~19 interception points, scattered across the codebase. High maintenance cost.
- Easy for a future view or service method to be added without a `DEMO_MODE` guard, silently breaking the demo contract.
- Disproportionate code surface for a feature whose primary requirement is "GET works, POST does not".

### 4. Do not implement

**Description:** Leave demo deployments out of scope; rely on screenshots and the `example.png` in the README.

**Pros:**
- Zero code change.

**Cons:**
- No way to give evaluators an interactive feel for the UI without making them run Docker.

## Decision

**Chosen:** Option 1 — single edge-level write blocker.

**Rationale:**

- Meets the user's "least invasive" constraint by a wide margin: one new middleware file, no service or view code touched, one entrypoint conditional, one template edit.
- The middleware boundary is the right place to enforce a global "no writes" invariant: every state-mutating user action in this app passes through it, and so does every future state-mutating user action that anyone adds later.
- The accepted trade-off (no fake-success workflows) is acceptable for the MVP. If a future demo experience needs click-through, Option 2 is a natural follow-up that builds on the same banner/middleware/entrypoint scaffolding without invalidating it.
- Forcing `REQUIRE_AUTH=False` when `DEMO_MODE=true` guarantees the demo is reachable without operator coordination — a minor "demo wins over auth" rule that is easy to document.

## Acceptance Criteria

- [ ] **AC-1**: With `DEMO_MODE=true`, `GET /` and `GET /instance/<slug>/` return HTTP 200 and render the dashboard / instance page normally.
- [ ] **AC-2**: With `DEMO_MODE=true`, `POST /api/instance/<slug>/backup/` returns a JSON body matching the existing error envelope (`{"status": "error", "message": "Demo mode: ..."}`) and **no** new `BackupRecord` row is created in the database.
- [ ] **AC-3**: With `DEMO_MODE=true`, `POST /instance/<slug>/delete/` returns an HTTP 302 redirect, attaches a Django `messages.warning(...)`, and the target `NodeRedConfig` row still exists after the request.
- [ ] **AC-4**: With `DEMO_MODE=true`, every page rendered through `base.html` contains a banner element with the text "Demo Mode" visible to the user.
- [ ] **AC-5**: With `DEMO_MODE=true`, container startup does **not** launch `runapscheduler` or `runwatcher`; `gunicorn` is the only application process.
- [ ] **AC-6**: With `DEMO_MODE=true` **and** `REQUIRE_AUTH=true` both set, an unauthenticated `GET /` returns HTTP 200 (not a redirect to `/login/`).
- [ ] **AC-7**: With `DEMO_MODE=false` (default), `DemoModeMiddleware` is a true no-op — a representative POST endpoint behaves exactly as before this change.
- [ ] **AC-8**: `GET /metrics` and `GET /health/` continue to return their normal Prometheus / health payloads regardless of `DEMO_MODE`.

## Consequences

### Positive

- Operators can host a public demo instance with a single env-var flip.
- The "no writes" invariant is enforced at one well-known location; future contributors do not need to remember to add per-view guards.
- Frontend toast UX is reused unchanged — the demo response slots into the existing `status: "error"` handler.
- Backwards compatible: `DEMO_MODE` defaults to false, so existing deployments are untouched.

### Negative

- The demo experience is read-only at the workflow level; clicking "Create Backup" produces a toast, not a new row. Documented as an MVP trade-off.
- `DEMO_MODE` overrides `REQUIRE_AUTH` (forces it off). Operators who want a password-protected demo would need a follow-up — currently out of scope.

### Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| A future view uses an HTTP method outside the standard set (e.g., `PATCH`) and the middleware fails to intercept it. | Low | Medium | Treat any non-safe method as blocked; only `GET`, `HEAD`, `OPTIONS`, `TRACE` are passed through. |
| Frontend treats `status: "error"` as a real failure and renders red rather than yellow. | Low | Low | The existing `showBanner` already styles all server-side rejections the same way; demo message is clearly worded. If a separate visual treatment is wanted later, add a `status: "demo"` branch in `app.js`. |
| `entrypoint.sh` `trap` references uninitialised PIDs after skipping background launches and exits non-zero. | Medium | Low | Initialise `SCHEDULER_PID=""` and `WATCHER_PID=""` defaults; the existing `2>/dev/null` already swallows the spurious `kill` errors but defaulting the variables is cleaner. |
| Operator runs `DEMO_MODE=true` against a real backup directory and assumes data is safe. | Low | Medium | Banner explicitly says "changes are not saved"; README documents that DEMO_MODE blocks writes but does not erase existing data. |

## Implementation Plan

- [ ] Add `DEMO_MODE` env-var read to `config/settings.py`; force `REQUIRE_AUTH = False` when on; register `backup.middleware.demo_mode.DemoModeMiddleware` immediately after `SimpleAuthMiddleware` in the `MIDDLEWARE` list.
- [ ] Create `backup/middleware/demo_mode.py` with `DemoModeMiddleware`. Pass through safe methods and `/login/`, `/logout/`. Intercept everything else: JSON envelope for `/api/` paths, `messages.warning` plus `Referer`-based redirect for HTML form posts.
- [ ] Extend `auth_context()` in `backup/context_processors.py` to expose `demo_mode = settings.DEMO_MODE`.
- [ ] Add a yellow banner block in `backup/templates/backup/base.html` immediately under `<nav>`, gated on `{% if demo_mode %}`, reusing the warning Tailwind palette already established in `_alert.html`.
- [ ] Wrap `runapscheduler` and `runwatcher` launches in `entrypoint.sh` with a `DEMO_MODE` conditional; default the PID variables so the existing trap stays safe.
- [ ] Add a `DEMO_MODE` row to the General env-var table in `README.md` and a short "Demo mode" subsection under Setup.
- [ ] Add `backup/tests/test_demo_mode.py` covering AC-1 through AC-8.
- [ ] User verifies end-to-end at `http://localhost:9473/` with `DEMO_MODE=true`; only then update status to **Implemented** and bump the version per the project's ADR workflow.

## Related ADRs

- [ADR 0001](./0001-nodered-backup-architecture.md) — original three-process architecture this option leaves in place.
- [ADR 0003](./0003-dockerization-and-basic-auth.md) — established `SimpleAuthMiddleware` and the env-var-driven authentication toggle that DEMO_MODE overrides.
- [ADR 0020](./0020-simplify-defaults-and-env-vars.md) — env-var convention reused for `DEMO_MODE`.
- [ADR 0026](./0026-auth-security-hardening.md) — current state of the auth middleware that DEMO_MODE sits next to in the chain.
- [ADR 0027](./0027-prometheus-metrics-endpoint.md) — established the pattern of "feature-flag env var that can switch a whole subsystem off cleanly".

## References

- Django middleware ordering: https://docs.djangoproject.com/en/6.0/topics/http/middleware/#middleware-ordering
- `request.META["HTTP_REFERER"]` semantics: https://docs.djangoproject.com/en/6.0/ref/request-response/#django.http.HttpRequest.META
