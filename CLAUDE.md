# FlowHistory

Django-based backup and restore tool for Node-RED flow files, running in Docker.

## Development Commands

All commands run inside the Docker container. Never use local `python`, `uv run`, or `manage.py` directly.

**Always rebuild the container** (`docker compose up -d --build`) after making code changes before testing in the browser.

### Build & Run

Use the repo's `docker-compose.yml` for development. It runs on port **9473** with no volumes (ephemeral data/backups) so it doesn't conflict with the production instance (port 9472 via `dca`).

```bash
docker compose up -d --build            # Rebuild and restart dev container
docker compose logs flowhistory --tail=50  # View logs
docker compose down                      # Stop
```

The dev container name is auto-generated (e.g. `nodered-backup-flowhistory-1`). Use `docker compose exec` instead of `docker exec` to target it without knowing the name.

### Tests
```bash
docker compose exec flowhistory python manage.py test backup -v2
```

### Migrations
```bash
docker compose exec flowhistory python manage.py makemigrations backup
docker compose exec flowhistory python manage.py migrate
```

### Django Management Commands
```bash
docker compose exec flowhistory python manage.py <command>
```

### Manual Testing (via browser)
```bash
# Dev UI: http://localhost:9473/ (no auth, debug mode, no Node-RED volumes)
# Production UI: http://192.168.1.76:9472/ (auth enabled, real data)
```

### Production Container

Use `dca` (alias for `~/docker/bin/dcwrap`, use full path in Bash tool) to manage the production container. It uses split stack files from `~/docker/`, not the repo's docker-compose.yml.

```bash
dca up -d --build flowhistory   # Rebuild and restart production
dca logs flowhistory --tail=50
dca down flowhistory
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

## Environment Variables

When adding new env vars, update all three places:
1. `README.md` — per-instance table and (if applicable) the Notifications global table
2. `backup/templates/backup/instance_add.html` — Add Instance UI
3. `backup/services/discovery_service.py` — `_KNOWN_FIELDS` set

## Tailwind CSS Conventions

- **Button styles**: Use shared CSS classes (`btn-primary`, `btn-secondary`, `btn-warning`, `btn-danger`) defined via `@apply` in `backup/static/backup/css/input.css`. Never inline the full Tailwind utility string for buttons — use the class instead.
- **Reusable components**: Check `backup/templates/backup/components/` for existing template includes (`_badge.html`, `_stat_card.html`, `_alert.html`, etc.) before duplicating Tailwind patterns.
- **When to extract**: If the same Tailwind utility string appears 3+ times across templates, extract it to either a CSS `@apply` class (for elements needing varied attributes like buttons) or a template component (for self-contained UI blocks like badges/cards).
