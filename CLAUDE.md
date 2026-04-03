# Node-RED Backup

Django-based backup and restore tool for Node-RED flow files, running in Docker.

## Development Commands

All commands run inside the Docker container. Never use local `python`, `uv run`, or `manage.py` directly.

**Always rebuild the container** (`docker compose up -d --build`) after making code changes before testing in the browser.

### Build & Run
```bash
docker compose up -d --build    # Rebuild and restart
docker compose logs --tail=50   # View logs
docker compose down             # Stop
```

### Tests
```bash
docker exec nodered-backup python manage.py test backup -v2
```

### Migrations
```bash
docker exec nodered-backup python manage.py makemigrations backup
docker exec nodered-backup python manage.py migrate
```

### Django Management Commands
```bash
docker exec nodered-backup python manage.py <command>
```

### Manual Backup (via API)
```bash
# Auth is enabled; use the web UI at http://192.168.1.76:9472/ or curl with session cookie
```

## Architecture

- Single container running 3 processes: gunicorn, APScheduler (`runapscheduler`), file watcher (`runwatcher`)
- Service layer pattern: `backup/services/` contains all business logic
- ADRs in `docs/adr/` track all architecture decisions
- SQLite database in `./data/`, backup archives in `./backups/`

## ADR Workflow

When an ADR implementation is complete:
1. Update the ADR status from "Proposed" to "Implemented"
2. Only commit and push **after the user has verified/tested** the feature is working

## Tailwind CSS Conventions

- **Button styles**: Use shared CSS classes (`btn-primary`, `btn-secondary`, `btn-warning`, `btn-danger`) defined via `@apply` in `backup/static/backup/css/input.css`. Never inline the full Tailwind utility string for buttons — use the class instead.
- **Reusable components**: Check `backup/templates/backup/components/` for existing template includes (`_badge.html`, `_stat_card.html`, `_alert.html`, etc.) before duplicating Tailwind patterns.
- **When to extract**: If the same Tailwind utility string appears 3+ times across templates, extract it to either a CSS `@apply` class (for elements needing varied attributes like buttons) or a template component (for self-contained UI blocks like badges/cards).
