# ADR 0001: Node-RED Backup Application Architecture

## Status
Partially Implemented

## Context

We need a self-hosted backup and restore solution for Node-RED flow files that:

- Runs in Docker alongside an existing Node-RED container
- Provides a web UI for configuration, backup history, and one-click restore
- Detects when flows.json changes on disk and creates backups automatically
- Parses the flows.json structure to show which tabs/flows changed between versions
- Supports scheduled backups, manual backups, and file-change-triggered backups
- Enforces retention policies
- Optionally restarts the Node-RED container after a restore

The architecture mirrors the proven pihole-checkpoint project (Django 5.x, APScheduler, single container with entrypoint.sh, service layer pattern) but replaces the API-based backup approach with local file watching and JSON diffing.

**Node-RED environment facts:**
- flows.json is a 1.5 MB JSON array (~51K lines) of objects, each with `id`, `type`, and often `z` (parent tab ID). Top-level tabs have `type: "tab"`.
- Additional files worth backing up: `flows_cred.json`, `settings.js`
- Node-RED data volume: `/media/cubxi/docker/volumes/nodered/data/`
- Container name: `nodered`, port 1881, user 1000:1000, network `automation_network`

**Key difference from pihole-checkpoint:** pihole-checkpoint calls a remote HTTP API to download a teleporter ZIP. This project watches a local file on a shared volume and copies it, which means file-change detection, debouncing, and JSON-level diffing are the core new problems.

## Decision

Build a Django 5.x web application following the pihole-checkpoint service layer pattern, adapted for file-based backup with change detection.

### 1. Technology Stack

| Component | Technology | Rationale |
|-----------|------------|-----------|
| Backend | Django 5.x, Python 3.12+ | Mirrors pihole-checkpoint |
| Database | SQLite (WAL mode) | Single-user, no separate service needed |
| Scheduler | APScheduler + django-apscheduler | No Redis/RabbitMQ needed |
| File Watching | watchdog | Mature inotify-based watcher, responsive |
| Frontend | Django Templates + Bootstrap 5 | Server-rendered, dark mode, offline assets |
| Web Server | Gunicorn | Same as pihole-checkpoint |
| Static Files | whitenoise | Same as pihole-checkpoint |
| Package Manager | uv | Same as pihole-checkpoint |
| Docker Interaction | docker (Python SDK) | Optional Node-RED container restart |

### 2. Project Structure

```
nodered-backup/
├── docker-compose.yml
├── Dockerfile
├── entrypoint.sh
├── pyproject.toml
├── manage.py
├── .env.example
├── config/
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── backup/
│   ├── models.py               # NodeRedConfig, BackupRecord
│   ├── views.py
│   ├── forms.py
│   ├── urls.py
│   ├── services/
│   │   ├── backup_service.py
│   │   ├── restore_service.py
│   │   ├── retention_service.py
│   │   ├── watcher_service.py   # watchdog file change detection
│   │   ├── diff_service.py      # JSON structural diff
│   │   ├── flow_parser.py       # Parse flows.json into tab/node tree
│   │   ├── docker_service.py    # Container restart via Docker socket
│   │   ├── credential_service.py
│   │   └── notifications/
│   ├── middleware/
│   │   └── simple_auth.py
│   ├── management/commands/
│   │   ├── runapscheduler.py
│   │   └── runwatcher.py
│   ├── templates/backup/
│   │   ├── base.html
│   │   ├── dashboard.html
│   │   ├── settings.html
│   │   ├── diff.html            # Visual diff viewer
│   │   └── login.html
│   ├── static/backup/
│   └── tests/
├── docs/adr/
├── data/                        # SQLite DB (volume)
└── backups/                     # Backup archives (volume)
```

### 3. Data Models

#### NodeRedConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | CharField(100) | "Node-RED" | Instance name |
| `flows_path` | CharField(500) | `/nodered-data/flows.json` | Path to flows.json in container |
| `backup_frequency` | CharField(10) | daily | hourly / daily / weekly |
| `backup_time` | TimeField | 03:00 | Time for daily/weekly |
| `backup_day` | SmallIntegerField | 0 | Day of week for weekly |
| `max_backups` | PositiveIntegerField | 20 | Max backups to keep |
| `max_age_days` | PositiveIntegerField | 30 | Max age in days |
| `is_active` | BooleanField | True | Enable scheduled backups |
| `watch_enabled` | BooleanField | True | File-change-triggered backups |
| `watch_debounce_seconds` | PositiveIntegerField | 30 | Debounce interval |
| `backup_credentials` | BooleanField | True | Also backup flows_cred.json |
| `backup_settings` | BooleanField | False | Also backup settings.js |
| `restart_on_restore` | BooleanField | False | Restart Node-RED after restore |
| `nodered_container_name` | CharField(100) | "nodered" | Container name for restart |
| `last_successful_backup` | DateTimeField | null | Timestamp |
| `last_backup_error` | TextField | blank | Error message |

#### BackupRecord

| Field | Type | Description |
|-------|------|-------------|
| `config` | ForeignKey | Parent config |
| `filename` | CharField(255) | Generated filename |
| `file_path` | CharField(500) | Full path on disk |
| `file_size` | BigIntegerField | Size in bytes |
| `checksum` | CharField(64) | SHA256 of flows.json |
| `status` | CharField(10) | success / failed |
| `error_message` | TextField | Error details |
| `trigger` | CharField(20) | "manual" / "scheduled" / "file_change" |
| `label` | CharField(200) | Optional user-supplied description |
| `tab_summary` | JSONField | List of tab names at backup time |
| `changes_summary` | JSONField | Which tabs/nodes changed vs previous backup |
| `includes_credentials` | BooleanField | Whether flows_cred.json included |
| `includes_settings` | BooleanField | Whether settings.js included |

### 4. File Watching (watchdog + debouncing)

- watchdog observer monitors `/nodered-data/` for flows.json modifications
- **Debouncing**: Timer-based, resets on each event, fires after 30s of quiet (configurable). Node-RED writes multiple times during a deploy.
- **Checksum deduplication**: Compare SHA256 against last backup — skip if unchanged
- Runs as a management command (`runwatcher`) alongside gunicorn and scheduler in entrypoint.sh

### 5. Change Detection (flow_parser + diff_service)

**Flow parser** groups the JSON array by tab:
- Nodes with `type: "tab"` are tabs
- Other nodes grouped by their `z` field (parent tab ID)
- Also tracks subflows, config nodes, global nodes

**Diff service** compares two parsed structures:
- Tabs added / removed / modified
- Per-tab: nodes added / removed / modified (by count)
- Stored in `BackupRecord.changes_summary` at backup time for fast dashboard display

### 6. Backup Strategy

Each backup is a `.tar.gz` archive containing:
- `flows.json` (always)
- `flows_cred.json` (if enabled and exists)
- `settings.js` (if enabled and exists)

**Naming**: `nodered_backup_{YYYYMMDD}_{HHMMSS}_{8-char-uuid}.tar.gz`

**Storage**: ~150 KB compressed per backup (1.5 MB JSON compresses ~10:1). At 20 max backups, total ~3 MB.

### 7. Restore Strategy

1. Verify archive exists and checksum matches
2. Create a pre-restore safety backup of current files
3. Extract and copy files to Node-RED data directory
4. Preserve file ownership (1000:1000) and permissions
5. Optionally restart Node-RED container via Docker socket

### 8. Docker Setup

```yaml
services:
  nodered-backup:
    build: .
    ports:
      - "8001:8000"
    volumes:
      - ./data:/app/data                                        # SQLite DB
      - ./backups:/app/backups                                  # Backup archives
      - /media/cubxi/docker/volumes/nodered/data:/nodered-data  # Node-RED data (rw)
      - /var/run/docker.sock:/var/run/docker.sock:ro             # Optional: restart
    environment:
      - NODERED_DATA_PATH=/nodered-data
      - NODERED_CONTAINER_NAME=nodered
      - TIME_ZONE=America/New_York
      - REQUIRE_AUTH=false
      - APP_PASSWORD=
      - DEBUG=false
      - ALLOWED_HOSTS=localhost,127.0.0.1
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health/"]
      interval: 30s
      timeout: 10s
      retries: 3
```

Single container running gunicorn + scheduler + file watcher via entrypoint.sh (same pattern as pihole-checkpoint, with a third process).

### 9. URL Structure

| URL | Method | Description |
|-----|--------|-------------|
| `/` | GET | Dashboard with backup history and status |
| `/settings/` | GET/POST | Configuration form |
| `/diff/<id>/` | GET | Diff viewer for a backup vs previous |
| `/diff/<id_a>/<id_b>/` | GET | Compare two backups |
| `/login/` | GET/POST | Authentication |
| `/health/` | GET | Health check |
| `/api/backup/` | POST | Create manual backup |
| `/api/backup/<id>/label/` | POST | Set/update backup label |
| `/backup/<id>/delete/` | POST | Delete backup |
| `/backup/<id>/download/` | GET | Download backup archive |
| `/api/restore/<id>/` | POST | Restore from backup |

### 10. Key Features

- **Backup on file change**: Near-instant backup when flows.json is modified (with debounce)
- **Scheduled backups**: Hourly / daily / weekly via APScheduler
- **Manual backups**: One-click from dashboard with optional label
- **User labels**: Annotate any backup with a description for easy identification
- **Visual diff viewer**: See which tabs/nodes changed, color-coded
- **One-click restore**: Restore files + optional container restart
- **Pre-restore safety backup**: Always created before overwriting
- **Credentials backup**: Optionally include flows_cred.json
- **Retention policies**: By count and age
- **Notifications**: Discord, Slack, Telegram, Pushbullet, Home Assistant
- **Dark mode**: Bootstrap 5 with toggle
- **Optional auth**: Simple password middleware

## Alternatives Considered

- **Polling vs watchdog**: Polling is simpler but higher latency and CPU. watchdog uses inotify for near-instant detection.
- **tar.gz vs zip**: tar.gz offers better compression and is more natural for Linux. pihole-checkpoint uses zip because Pi-hole exports zip.
- **deepdiff library vs structural diff**: deepdiff gives property-level detail but is overkill and slow on 51K-line files. Tab/node-level structural diff is fast and sufficient.
- **Docker socket vs no restart**: Node-RED doesn't auto-reload flows from disk. Socket-based restart is opt-in for those who want it.
- **Single container vs multi-container**: Single container is simpler to deploy. SQLite with WAL mode handles the low write concurrency fine.

## Consequences

**Positive:**
- Familiar architecture for anyone who has worked on pihole-checkpoint
- Automatic backup on file change gives near-zero recovery point objective
- JSON-level diffing provides meaningful change context
- Single container keeps deployment simple

**Negative:**
- Three processes in one container requires careful process management (mitigated by proven entrypoint.sh pattern)
- watchdog + inotify requires bind-mount volume (standard for home-lab setups)
- Docker socket access allows container control (mitigated by read-only mount, opt-in)
- SQLite with three writers can hit contention (mitigated by WAL mode + low write frequency)

## Todos

- [x] Initialize Django project (config/, backup/ apps)
- [x] Create models and migrations
- [x] Implement flow_parser.py
- [x] Implement backup_service.py with tar.gz creation
- [ ] Implement watcher_service.py with watchdog + debouncing
- [ ] Implement diff_service.py
- [x] Implement restore_service.py
- [x] Implement docker_service.py for container restart
- [ ] Implement retention_service.py
- [ ] Create management commands (runapscheduler, runwatcher)
- [ ] Build dashboard, settings, diff viewer templates
- [x] Create Dockerfile, docker-compose.yml, entrypoint.sh
- [ ] Add notification service
- [x] Add optional auth middleware
- [ ] Write tests
- [x] Create README.md and .env.example
