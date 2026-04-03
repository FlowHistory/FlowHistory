# Node-RED Backup

Django-based backup and restore tool for Node-RED flow files, running in Docker.

## Development Commands

All commands run inside the Docker container. Never use local `python`, `uv run`, or `manage.py` directly.

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
