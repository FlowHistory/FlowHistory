# ADR 0002: Django App Bootstrapping

## Status
Proposed

## Context

With the architecture defined in ADR 0001, we need to bootstrap the actual Django project — create the project skeleton, configure settings, set up the `backup` app with its initial model, build a basic home page, and wire up the minimal API endpoints. This ADR covers the bare-minimum bootstrapping to get a running Django app with a functional UI and API surface that subsequent work (services, file watching, diffing) can build on.

The goal is a working container that serves:
- A home page (dashboard) showing app status and an empty backup list
- A health check endpoint
- A manual backup API endpoint (stub)
- Static assets served via whitenoise

## Decision

### 1. Project Initialization

Use `uv` as the package manager (matching pihole-checkpoint). Initialize with:

```
uv init
uv add django gunicorn whitenoise django-apscheduler
```

Create the Django project and app:

```
django-admin startproject config .
django-admin startapp backup
```

### 2. Project Layout (Initial Files)

Only the files needed for bootstrapping — services, management commands, and advanced features come later.

```
nodered-backup/
├── pyproject.toml
├── manage.py
├── config/
│   ├── __init__.py
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── backup/
│   ├── __init__.py
│   ├── admin.py
│   ├── apps.py
│   ├── models.py          # NodeRedConfig + BackupRecord (full schema from ADR 0001)
│   ├── views.py            # Dashboard view + health check
│   ├── urls.py
│   ├── templates/backup/
│   │   ├── base.html       # Bootstrap 5 layout with dark mode toggle
│   │   └── dashboard.html  # Home page
│   └── static/backup/
│       ├── css/
│       │   └── style.css   # Custom overrides
│       └── js/
│           └── app.js      # Dark mode toggle, basic interactivity
├── docs/adr/
├── data/                   # SQLite DB (gitignored)
└── backups/                # Backup archives (gitignored)
```

### 3. Settings Configuration

`config/settings.py` key decisions:

| Setting | Value | Rationale |
|---------|-------|-----------|
| `DATABASES` | SQLite at `/app/data/db.sqlite3` with WAL mode | Single-user, no external DB needed |
| `STATIC_URL` | `/static/` | Standard |
| `STATICFILES_STORAGE` | `whitenoise.storage.CompressedManifestStaticFilesStorage` | Serve static files without nginx |
| `TIME_ZONE` | From `TIME_ZONE` env var, default `America/New_York` | User-configurable |
| `SECRET_KEY` | From `SECRET_KEY` env var, auto-generated fallback | Security |
| `DEBUG` | From `DEBUG` env var, default `False` | Production-safe default |
| `ALLOWED_HOSTS` | From `ALLOWED_HOSTS` env var, split on comma | Docker networking flexibility |

All configuration via environment variables, no `.env` file parsing in settings (Docker handles env).

### 4. Models (Initial Migration)

Create both models from ADR 0001 (`NodeRedConfig` and `BackupRecord`) in the initial migration. Even though services aren't built yet, having the full schema from day one avoids migration churn.

### 5. Home Page (Dashboard)

The dashboard at `/` shows:
- App title and Node-RED instance name
- Connection status (whether flows.json path is readable)
- Backup count and last backup timestamp
- Empty backup list table (columns: Date, Trigger, Label, Size, Actions) — ready for data once backup_service exists
- "Create Backup" button (wired to `/api/backup/` POST, initially returns stub response)

Built with:
- **Bootstrap 5** loaded from local static files (offline-capable, no CDN)
- **Dark mode** toggle using Bootstrap's `data-bs-theme` attribute
- **Responsive** layout for mobile access

### 6. Initial URL Routes

| URL | Method | View | Description |
|-----|--------|------|-------------|
| `/` | GET | `dashboard` | Home page |
| `/health/` | GET | `health_check` | Returns 200 + JSON `{"status": "ok"}` |
| `/api/backup/` | POST | `create_backup` | Stub — returns 501 until backup_service is built |

Keep the URL surface minimal. Settings page, diff viewer, restore, auth, and remaining API endpoints are added in later ADRs/implementation phases.

### 7. Base Template Structure

`base.html` provides:
- HTML5 doctype with `data-bs-theme` for dark mode
- Bootstrap 5 CSS/JS from static files
- Navigation bar with app name
- Flash messages / Django messages framework
- Content block for child templates
- Footer with version info

### 8. Health Check

Simple view that returns `{"status": "ok"}` with 200 status. Used by Docker `HEALTHCHECK`. No database check needed at this stage — if Django is responding, the app is up.

### 9. .gitignore

Ignore:
- `data/` (SQLite database)
- `backups/` (backup archives)
- `__pycache__/`, `*.pyc`
- `.env`
- `staticfiles/` (collected static files)
- `*.sqlite3`

## Alternatives Considered

- **Start with API-only (no templates)**: Would require a separate frontend later. Server-rendered templates with Bootstrap are faster to ship and match the pihole-checkpoint pattern.
- **Use Django REST Framework for APIs**: Overkill for a handful of endpoints. Plain Django `JsonResponse` views are sufficient.
- **CDN for Bootstrap**: Requires internet access. Local static files work in air-gapped home lab environments.
- **Create models incrementally**: Adding fields later means migration churn. Defining the full schema upfront from ADR 0001 is cleaner.
- **Use cookiecutter-django or similar template**: Too much boilerplate for a focused single-app project.

## Consequences

**Positive:**
- Running app from day one — all subsequent features plug into an existing, testable shell
- Full model schema means services can be developed independently without migration coordination
- Bootstrap 5 dark mode and responsive layout handled early, not retrofitted
- Health check enables Docker orchestration immediately

**Negative:**
- Stub API endpoint returns 501 until backup_service is implemented (acceptable — dashboard shows this clearly)
- Bootstrap static files add ~300 KB to the image (negligible)

## Todos

- [ ] Initialize project with `uv` and install dependencies
- [ ] Create Django project (`config/`) and app (`backup/`)
- [ ] Configure `settings.py` with env-var-driven settings
- [ ] Define `NodeRedConfig` and `BackupRecord` models
- [ ] Create and run initial migration
- [ ] Build `base.html` with Bootstrap 5 and dark mode
- [ ] Build `dashboard.html` with status cards and empty backup table
- [ ] Add dashboard view, health check view, and stub backup API view
- [ ] Wire up URL routes
- [ ] Add Bootstrap 5 static files (CSS + JS)
- [ ] Create `.gitignore`
- [ ] Verify app runs with `python manage.py runserver`
