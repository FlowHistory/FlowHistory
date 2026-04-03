# nodered-backup

A Django web application for automated backup and restore of Node-RED flow files. Watches the `flows.json` file for changes, creates versioned backups with flow-level change detection, and provides a web UI to browse, compare, label, and restore backups.

## Overview

- **Stack**: Django 5.x, Python 3.12+, SQLite (WAL), APScheduler, watchdog, Gunicorn, Bootstrap 5
- **Deployment**: Single Docker container running gunicorn + scheduler + file watcher via `entrypoint.sh`
- **Port**: 8001 (maps to 8000 inside container)

### Key Features

- **File watching**: watchdog monitors `flows.json` for changes with debouncing (30s default) and checksum deduplication
- **Scheduled backups**: Hourly / daily / weekly via APScheduler
- **Manual backups**: One-click from dashboard with optional user label
- **Flow-level diffing**: Parses flows.json to identify which tabs/nodes changed between versions
- **Visual diff viewer**: Color-coded view of added/removed/modified tabs and node counts
- **One-click restore**: Copies backup files back + optional Node-RED container restart via Docker socket
- **Pre-restore safety backup**: Always created before overwriting current files
- **Multi-file backup**: flows.json (always), flows_cred.json (optional), settings.js (optional)
- **Retention policies**: By max count and max age
- **Notifications**: Discord, Slack, Telegram, Pushbullet, Home Assistant
- **Dark mode**, **optional password auth**

### Architecture

Follows the pihole-checkpoint service layer pattern:

```
backup/services/
├── backup_service.py       # Backup creation (tar.gz archives)
├── restore_service.py      # File restore + optional container restart
├── retention_service.py    # Cleanup old backups
├── watcher_service.py      # watchdog file change detection + debounce
├── diff_service.py         # JSON structural diff (tab/node level)
├── flow_parser.py          # Parse flows.json into tab/node tree
├── docker_service.py       # Node-RED container restart via Docker socket
├── credential_service.py   # Env-var configuration
└── notifications/          # Multi-provider notification system
```

### Docker Volumes

| Mount | Path in container | Purpose |
|-------|-------------------|---------|
| `./data` | `/app/data` | SQLite database |
| `./backups` | `/app/backups` | Backup archives |
| Node-RED data dir | `/nodered-data` | Watched flows.json (read-write, needed for restore) |
| Docker socket | `/var/run/docker.sock` | Optional: restart Node-RED after restore |

### Node-RED Environment

- **Container**: `nodered` on `automation_network`
- **Data volume**: `/media/cubxi/docker/volumes/nodered/data/`
- **flows.json**: ~1.5 MB JSON array with tabs (Downstairs, Upstairs, Outside, Locations, Notifications, Misc, Windows, Cameras, AI)
- **Port**: 1881

## ADRs (Architecture Decision Records)

All architectural decisions are tracked in `docs/adr/`.

- **Full ADR index with implementation status**: `docs/adr/0000-adr-index.md`
- **ADR template and format guide**: `docs/adr/README.md`

### Creating a new ADR

1. Use the next sequential number (check the index for the latest).
2. Follow the template in `docs/adr/README.md`.
3. Add an entry to the index in `docs/adr/0000-adr-index.md`.
4. Set the initial status to **Proposed**.
5. Update the status to **Implemented** once the decision is fully applied.
6. If an ADR is replaced, mark it **Superseded** and note the replacement ADR number.
