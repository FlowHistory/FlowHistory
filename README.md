# FlowHistory

A self-hosted backup and restore tool for Node-RED flow files. Runs as a Docker container alongside your Node-RED instance. Automatically detects flow changes, creates compressed backups, and provides a web UI for managing backups, viewing diffs, and restoring.

## Features

- **Automatic backups on file change** -- watches flows.json via inotify with configurable debounce
- **Scheduled backups** -- hourly, daily, or weekly via APScheduler
- **Manual backups** -- one-click from the dashboard
- **Visual diff viewer** -- see which tabs, subflows, and nodes changed between any two backups, with field-level diffs
- **One-click restore** -- restores files and optionally restarts the Node-RED container
- **Pre-restore safety backup** -- always created before overwriting current flows
- **Checksum deduplication** -- skips backup if flows.json hasn't changed (SHA256)
- **Retention policies** -- by max count and max age, with protection for recent safety backups
- **Credentials and settings backup** -- optionally include flows_cred.json and settings.js
- **Labels** -- annotate backups with descriptions
- **Download backups** -- download any backup archive directly from the UI
- **Dark mode** -- with system preference detection and manual toggle
- **Optional password auth** -- simple shared password via environment variable
- **Health check endpoint** -- for Docker healthcheck integration

## Setup

### 1. Clone and configure

```bash
git clone <repo-url> flowhistory
cd flowhistory
cp .env.example .env
```

Edit `.env`:

```env
SECRET_KEY=change-me-to-a-random-string
DEBUG=false
ALLOWED_HOSTS=localhost,127.0.0.1,192.168.1.76
TIME_ZONE=America/New_York
NODERED_DATA_PATH=/nodered-data
NODERED_CONTAINER_NAME=nodered
REQUIRE_AUTH=false
APP_PASSWORD=
```

### 2. Configure volumes in docker-compose.yml

Update the volume paths to match your environment:

```yaml
volumes:
  - ./data:/app/data                          # SQLite database
  - ./backups:/app/backups                    # Backup archives
  - /path/to/nodered/data:/nodered-data       # Node-RED data directory (must contain flows.json)
  - /var/run/docker.sock:/var/run/docker.sock  # Optional: enables container restart on restore
```

### 3. Build and run

```bash
docker compose up -d --build
```

The UI is available at `http://<host>:<port>/` (default port 8000, mapped in docker-compose.yml).

### 4. Configure via the Settings page

Visit `/settings/` to set backup frequency, retention limits, file watching options, and which files to include.

## Architecture

Single container running three processes via entrypoint.sh:

- **gunicorn** -- serves the Django web application
- **APScheduler** -- runs scheduled backups and retention cleanup
- **watchdog** -- monitors flows.json for changes and triggers backups

All business logic lives in `backup/services/`. Data is stored in SQLite (WAL mode). Backups are tar.gz archives stored on disk.

## Development

All commands run inside the Docker container:

```bash
# Rebuild after code changes
docker compose up -d --build

# Run tests
docker exec flowhistory python manage.py test backup -v2

# View logs
docker compose logs --tail=50

# Run migrations
docker exec flowhistory python manage.py makemigrations backup
docker exec flowhistory python manage.py migrate
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/backup/` | POST | Create a manual backup |
| `/api/backup/<id>/label/` | POST | Set or update a backup label |
| `/api/restore/<id>/` | POST | Restore from a backup |
| `/health/` | GET | Health check (returns `{"status": "ok"}`) |

## License

MIT
