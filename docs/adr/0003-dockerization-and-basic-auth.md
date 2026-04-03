# ADR 0003: Dockerization and Basic Auth

## Status
Implemented

## Context

With the Django app bootstrapped (ADR 0002), we need to containerize it for deployment and add optional password-based authentication. The app runs alongside a Node-RED container on the same Docker host, so the Docker setup must support:

- Bind-mounting the Node-RED data directory for file watching and restore
- Optional Docker socket access for container restart
- Persistent storage for SQLite and backup archives
- All configuration via environment variables (`.env` file for docker-compose)

Authentication needs are simple — this is a single-user home-lab app. A full user/role system is overkill. We need a single shared password that gates access when enabled, with a clean login screen and session-based persistence so the user doesn't re-enter the password on every page.

## Decision

### 1. Dockerfile

Multi-stage build using Python 3.12 slim base:

```dockerfile
FROM python:3.12-slim AS builder

WORKDIR /app
COPY pyproject.toml uv.lock ./

RUN pip install uv && \
    uv sync --frozen --no-dev

FROM python:3.12-slim

WORKDIR /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

COPY . .

RUN python manage.py collectstatic --noinput

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/health/ || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
```

Key decisions:
- **Multi-stage build** to keep image small (no build tools in final image)
- **`uv sync`** for reproducible installs from lockfile
- **`collectstatic` at build time** so static files are baked into the image
- **`curl` for healthcheck** (lightweight, reliable)
- **No `USER` directive yet** — runs as root inside container for Docker socket access and volume permissions. Can be locked down later if needed.

### 2. entrypoint.sh

Runs three processes (same pattern as pihole-checkpoint):

```bash
#!/bin/bash
set -e

# Run migrations on startup
python manage.py migrate --noinput

# Start gunicorn in background
gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 2 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile - &

# Start scheduler in background (when implemented)
# python manage.py runapscheduler &

# Start file watcher in foreground (when implemented)
# python manage.py runwatcher

# For now, wait on gunicorn
wait
```

Migrations run automatically on container start — no manual step needed after upgrades.

### 3. docker-compose.yml

```yaml
services:
  nodered-backup:
    build: .
    container_name: nodered-backup
    ports:
      - "9472:8000"
    volumes:
      - ./data:/app/data
      - ./backups:/app/backups
      - /media/cubxi/docker/volumes/nodered/data:/nodered-data
      - /var/run/docker.sock:/var/run/docker.sock:ro
    env_file:
      - .env
    restart: unless-stopped
    networks:
      - automation_network

networks:
  automation_network:
    external: true
```

- **Port 9472** externally maps to 8000 inside (avoids conflict with other services)
- **`env_file: .env`** keeps secrets out of compose file and version control
- **`automation_network`** is external — shared with Node-RED and other home-lab containers
- **Docker socket** mounted read-only for optional container restart

### 4. Environment Variables (.env file)

Create `.env.example` as a template (committed to git). Actual `.env` is gitignored.

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | auto-generated | Django secret key (should be set for production) |
| `DEBUG` | `false` | Enable Django debug mode |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated allowed hostnames |
| `TIME_ZONE` | `America/New_York` | Application timezone |
| `NODERED_DATA_PATH` | `/nodered-data` | Path to Node-RED data inside container |
| `NODERED_CONTAINER_NAME` | `nodered` | Node-RED container name for restart |
| `REQUIRE_AUTH` | `false` | Enable password authentication |
| `APP_PASSWORD` | *(empty)* | Password for login (required when `REQUIRE_AUTH=true`) |

### 5. Basic Auth — Middleware Approach

Use a custom Django middleware (`backup/middleware/simple_auth.py`) that:

1. Checks if `REQUIRE_AUTH` env var is `true`
2. If auth is not required, all requests pass through (no login needed)
3. If auth is required:
   - Exempt paths: `/login/`, `/health/`, `/static/` (health check must work without auth for Docker)
   - Check for `authenticated` flag in the Django session
   - If not authenticated, redirect to `/login/`
   - If authenticated, allow the request

This is a session-based approach — user logs in once and the session cookie keeps them authenticated until it expires or they log out.

**Why middleware, not Django's built-in auth system:**
- No user accounts to manage — just a single shared password from an env var
- No username needed — it's a single-user app
- Django's auth requires creating a superuser and database records
- Middleware is ~30 lines of code and does exactly what's needed

### 6. Login View and Template

**Login view** (`/login/`):
- GET: Render login form
- POST: Compare submitted password against `APP_PASSWORD` env var
  - Match: Set `request.session["authenticated"] = True`, redirect to `/`
  - No match: Re-render form with error message
- Uses Django's CSRF protection

**Logout view** (`/logout/`):
- POST only (CSRF protected)
- Flush session, redirect to `/login/`

**Login template** (`backup/templates/backup/login.html`):
- Clean, centered card layout using Bootstrap 5
- App name/logo at top
- Single password field (no username)
- "Sign In" button
- Error message display for wrong password
- Respects dark mode from `base.html`
- No "forgot password" or "register" links (single-user app)

### 7. Settings Changes

Add to `config/settings.py`:

```python
# Authentication
REQUIRE_AUTH = os.environ.get("REQUIRE_AUTH", "false").lower() in ("true", "1", "yes")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
LOGIN_URL = "/login/"
```

Add middleware to `MIDDLEWARE` list (after `SessionMiddleware`, before other middleware):

```python
"backup.middleware.simple_auth.SimpleAuthMiddleware",
```

### 8. URL Routes (Auth-Related)

| URL | Method | View | Description |
|-----|--------|------|-------------|
| `/login/` | GET/POST | `login_view` | Login page |
| `/logout/` | POST | `logout_view` | End session, redirect to login |

## Alternatives Considered

- **Django's built-in auth (`django.contrib.auth`)**: Requires user model, superuser creation, username+password. Overkill for a single shared password. Would need a management command or entrypoint step to auto-create the admin user from env vars.
- **HTTP Basic Auth (via middleware or reverse proxy)**: No logout mechanism, ugly browser prompt instead of a styled login page, credentials sent on every request.
- **Token-based auth (API key in header)**: Better for API clients, worse for browser-based usage. No session persistence.
- **No auth (rely on network isolation)**: Fine for some setups, but this app can trigger restores that overwrite Node-RED files — a login gate adds a useful safety layer.
- **Docker secrets instead of env vars**: More secure for orchestrated setups (Swarm/K8s), but adds complexity. Env vars via `.env` file is standard for docker-compose home-lab deployments.
- **Single-stage Dockerfile**: Simpler but results in a larger image with build tools included.

## Consequences

**Positive:**
- One-command deployment with `docker compose up -d`
- All config in a single `.env` file — no editing Python files
- Auth is fully optional — disabled by default for frictionless setup
- Login screen is clean and matches the app's Bootstrap 5 dark mode design
- Health check works without auth (Docker can monitor the container)
- Multi-stage build keeps image small
- Auto-migration on startup means zero-downtime upgrades

**Negative:**
- Single shared password (no per-user accounts) — acceptable for single-user home-lab
- Password stored in plaintext in `.env` file — standard for docker-compose deployments, mitigated by gitignoring `.env`
- Running as root in container — needed for Docker socket and volume permissions, can be hardened later
- Three processes in one container requires `wait` in entrypoint — proven pattern from pihole-checkpoint

## Todos

- [ ] Create `Dockerfile` with multi-stage build
- [ ] Create `entrypoint.sh` with migration + gunicorn startup
- [ ] Create `docker-compose.yml` with volumes, env_file, and network
- [ ] Create `.env.example` with all variables documented
- [ ] Add `.env` to `.gitignore`
- [ ] Add `REQUIRE_AUTH` and `APP_PASSWORD` settings to `config/settings.py`
- [ ] Create `backup/middleware/simple_auth.py`
- [ ] Add `SimpleAuthMiddleware` to `MIDDLEWARE` in settings
- [ ] Create login view and logout view in `backup/views.py`
- [ ] Create `backup/templates/backup/login.html` template
- [ ] Add `/login/` and `/logout/` URL routes
- [ ] Add logout button to `base.html` navigation (shown when auth is enabled)
- [ ] Build and test with `docker compose up --build`
- [ ] Verify health check works without auth
- [ ] Verify login flow: redirect -> login -> dashboard
- [ ] Verify logout flow: logout -> redirect to login
- [ ] Verify disabled auth: all pages accessible without login
