# ADR 0026: Auth Security Hardening

**Status:** Proposed
**Date:** 2026-04-14
**Author:** Leonardo Merza

## Context

### Background

FlowHistory uses a custom session-based auth system implemented in ADR 0003. It's a single shared password checked via middleware — appropriate for a single-user home-lab app. A security review identified three weaknesses in the current implementation.

### Current State

- **Timing-unsafe comparison:** `login_view` in `backup/views/auth.py` uses `password == settings.APP_PASSWORD` (Python `==`). String equality in Python short-circuits on the first mismatched character, leaking password length and prefix information through response time differences.
- **No brute-force protection:** The `/login/` endpoint accepts unlimited attempts with no rate limiting or lockout. An attacker on the local network could script a dictionary attack.
- **Empty password accepted:** `APP_PASSWORD` defaults to `""`. If a user sets `REQUIRE_AUTH=true` but forgets `APP_PASSWORD`, an empty string submission would authenticate successfully.

### Requirements

- Fix all three vulnerabilities with minimal complexity
- No new dependencies — keep the project's lightweight pattern
- Don't break the existing dev workflow (`REQUIRE_AUTH=false` must still work)

### Constraints

- Single-container deployment with 2 gunicorn workers (in-memory state not shared across workers)
- LAN-only exposure — not internet-facing
- Must maintain backward compatibility with existing `.env` files

## Options Considered

### 1. Timing-Safe Comparison

Only one viable approach: replace `==` with `hmac.compare_digest()` from Python's stdlib. This function uses a constant-time algorithm that doesn't short-circuit, preventing timing attacks. No alternatives were considered because this is the standard, zero-cost fix.

### 2. Brute-Force Protection

#### Option A: In-memory counter (chosen)

**Description:** Track failed attempts per IP in a Python dict with TTL-based expiry. After N failures in M minutes, block that IP for a lockout period.

**Pros:**
- Zero dependencies
- ~20 lines of code
- Fits the existing `SimpleAuthMiddleware` pattern
- No database writes on login attempts

**Cons:**
- State lost on container restart
- Not shared across gunicorn workers (each worker has its own dict)

#### Option B: Session-based counter

**Description:** Store failed attempt count in `request.session`.

**Pros:**
- Persists across restarts (SQLite-backed sessions)

**Cons:**
- Trivially bypassed by clearing cookies or using incognito
- Attacker controls their own session — provides no real protection

#### Option C: Database-backed counter

**Description:** New `LoginAttempt` model tracking IP, timestamp, and outcome.

**Pros:**
- Persists across restarts and workers
- Provides audit trail

**Cons:**
- New model + migration for a single-user app
- DB write on every login attempt
- Needs cleanup job to prevent table growth
- Heavier than the project's style warrants

#### Option D: django-ratelimit package

**Description:** Third-party decorator-based rate limiting.

**Pros:**
- Well-tested, one-line decorator

**Cons:**
- New dependency for a single endpoint
- Requires configuring Django cache backend
- Against the project's minimal-dependency pattern

### 3. Empty Password Validation

#### Option A: Refuse to start (chosen)

**Description:** Add a Django system check that raises `ImproperlyConfigured` during startup if `REQUIRE_AUTH=true` and `APP_PASSWORD` is empty.

**Pros:**
- Fail-fast — impossible to run misconfigured
- Clear error message in container logs

**Cons:**
- Container won't start until user fixes config

#### Option B: Log warning, disable auth

**Description:** Fall back to `REQUIRE_AUTH=false` with a log warning.

**Cons:**
- Silently disables a security feature the user explicitly enabled

#### Option C: Log warning, keep auth enabled

**Description:** Auth stays on, but no password matches — locks everyone out.

**Cons:**
- App runs but is completely unusable; confusing failure mode

## Decision

Apply all three fixes:

1. **Timing-safe comparison:** Replace `password == settings.APP_PASSWORD` with `hmac.compare_digest(password, settings.APP_PASSWORD)` in `login_view`.

2. **In-memory brute-force counter:** Add rate limiting logic to `SimpleAuthMiddleware` that tracks failed login POSTs per IP. Configuration:
   - `MAX_LOGIN_ATTEMPTS = 5` (failures before lockout)
   - `LOGIN_ATTEMPT_WINDOW = 300` (5-minute window)
   - `LOCKOUT_DURATION = 900` (15-minute lockout)
   - These are code constants, not env vars — no reason to make them configurable for a home-lab app.
   - When locked out, the login page renders with a "Too many attempts, try again later" message.
   - The per-worker limitation is acceptable: with 2 workers, an attacker gets at most 10 attempts per window instead of 5. Good enough for LAN protection.

3. **Startup validation:** Add a Django system check in `backup/apps.py` that raises `ImproperlyConfigured` if `REQUIRE_AUTH` is true and `APP_PASSWORD` is empty. This runs before any requests are served.

**Rationale:** All three fixes are minimal, zero-dependency, and follow the project's existing patterns. The in-memory counter was chosen over database-backed alternatives because the threat model (LAN attacker) doesn't warrant persistence across restarts, and it avoids adding a model/migration for a non-functional concern.

## Consequences

### Positive
- Timing attacks on password comparison are eliminated
- Brute-force attacks are throttled to 5 attempts per 5-minute window per worker
- Misconfigured deployments fail fast with a clear error message
- No new dependencies or database changes

### Negative
- Rate limit state is lost on container restart (attacker gets a fresh window)
- Rate limit is per-worker, not global (2x the intended threshold with 2 workers)

### Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Attacker restarts container to reset rate limit | Low | Low | Requires Docker socket access, which is a bigger problem than login brute-force |
| Legitimate user locked out by typos | Low | Low | 5 attempts is generous; 15-min lockout is short; container restart clears it |
| Per-worker split dilutes rate limit | Medium | Low | 10 attempts/window is still effective for LAN threat model |

## Implementation Plan

- [ ] Add `hmac` import and replace `==` with `hmac.compare_digest()` in `backup/views/auth.py`
- [ ] Add rate limiting logic to `backup/middleware/simple_auth.py` (failed attempt tracking, lockout check, lockout response)
- [ ] Pass lockout error context to `login.html` template and display a lockout message
- [ ] Add Django system check in `backup/apps.py` to validate `APP_PASSWORD` is non-empty when `REQUIRE_AUTH=true`
- [ ] Add tests in `backup/tests/test_auth.py` for all three changes:
  - Timing-safe comparison still authenticates correctly
  - Rate limiting blocks after N failures
  - Rate limiting resets after window expires
  - Startup check raises error on empty password
  - Startup check passes when auth is disabled

## Related ADRs

- [ADR 0003](./0003-dockerization-and-basic-auth.md) — Original auth implementation being hardened

## Files to Modify

| File | Change |
|------|--------|
| `backup/views/auth.py` | Use `hmac.compare_digest()` for password check |
| `backup/middleware/simple_auth.py` | Add in-memory rate limiting for login attempts |
| `backup/apps.py` | Add system check for empty `APP_PASSWORD` |
| `backup/templates/backup/login.html` | Add lockout message display |
| `backup/tests/test_auth.py` | Add tests for all three hardening measures |
