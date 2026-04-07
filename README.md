# FlowHistory

A self-hosted backup and restore tool for Node-RED flow files. Runs as a Docker container that manages backups for one or more Node-RED instances — local (file-based) or remote (API-based). Automatically detects flow changes, creates compressed backups, and provides a web UI for managing backups, viewing diffs, and restoring.

## Features

- **Multi-instance support** — manage backups for multiple Node-RED instances from a single deployment
- **Local and remote instances** — watch local files via inotify or poll remote instances via the Node-RED Admin API
- **Automatic backups on change** — file watching (local) or API polling (remote) with configurable intervals
- **Scheduled backups** — hourly, daily, or weekly via APScheduler
- **Manual backups** — one-click from the dashboard
- **Visual diff viewer** — see which tabs, subflows, and nodes changed between any two backups, with field-level diffs
- **One-click restore** — restores files (local) or deploys flows via API (remote), optionally restarts the container
- **Pre-restore safety backup** — always created before overwriting current flows
- **Checksum deduplication** — skips backup if flows haven't changed (SHA256)
- **Retention policies** — by max count and max age, with protection for pinned and recent safety backups
- **Credentials and settings backup** — optionally include flows_cred.json and settings.js (local only)
- **Labels and notes** — annotate backups with descriptions
- **Notifications** — alerts via Discord, Slack, Telegram, Pushbullet, or Home Assistant for backup failures, restores, and more
- **Dark mode** — with system preference detection and manual toggle
- **Optional password auth** — simple shared password via environment variable
- **Health check endpoint** — for Docker healthcheck integration

## Setup

### 1. Clone and configure

```bash
git clone <repo-url> flowhistory
cd flowhistory
cp .env.example .env
```

Edit `.env` with your general settings:

```env
DEBUG=false
ALLOWED_HOSTS=localhost,127.0.0.1,192.168.1.76
TIME_ZONE=America/New_York
REQUIRE_AUTH=true
APP_PASSWORD=changeme
```

### 2. Configure volumes

Update the volume paths in `docker-compose.yml` to match your environment:

```yaml
volumes:
  - ./data:/app/data                          # SQLite database
  - ./backups:/app/backups                    # Backup archives
  - /path/to/nodered/data:/nodered-data       # Node-RED data directory (local instances)
  - /var/run/docker.sock:/var/run/docker.sock  # Optional: enables container restart on restore
```

### 3. Add instances

Instances are configured via environment variables in your `.env` file using the `FLOWHISTORY_{PREFIX}_{FIELD}` convention. Each instance gets a unique prefix (e.g., `LOCAL`, `PROD`, `SHED`).

#### Local instance

For Node-RED running on the same Docker host with a mounted volume:

```env
FLOWHISTORY_LOCAL_FLOWS_PATH=/nodered-data/flows.json
FLOWHISTORY_LOCAL_NAME=My Node-RED
```

#### Remote instance

For Node-RED running on another server, accessed via the Admin API:

```env
FLOWHISTORY_SHED_URL=http://192.168.1.114:1880
FLOWHISTORY_SHED_NAME=Shed
FLOWHISTORY_SHED_USER=flowhistory
FLOWHISTORY_SHED_PASS=yourpassword
```

Credentials (`_USER`, `_PASS`) are read from the environment at runtime and never stored in the database.

### 4. Creating a Node-RED user for remote access

Remote instances require access to the Node-RED Admin API. Create a dedicated user with minimal permissions.

**Generate a password hash** inside the Node-RED container:

```bash
docker exec <nodered-container> node -e "require('bcryptjs').hash('yourpassword', 8, (e,h) => console.log(h))"
```

**Add the user** to the Node-RED instance's `settings.js`:

```js
adminAuth: {
    type: "credentials",
    users: [{
        username: "flowhistory",
        password: "<paste bcrypt hash here>",
        permissions: ["flows.read"]
    }]
}
```

**Permissions:**

| Permission | Required for |
|------------|-------------|
| `flows.read` | Backup (polling flows) |
| `flows.write` | Restore (deploying flows back) |

Use `["flows.read"]` for backup-only access. Add `"flows.write"` if you want to restore flows from FlowHistory back to this instance.

Restart the Node-RED instance after editing `settings.js`.

### 5. Build and run

```bash
docker compose up -d --build
```

The UI is available at `http://<host>:9472/`. With a single instance, the dashboard auto-redirects to it. With multiple instances, you'll see an instance overview grid.

## Environment Variables

### General

| Variable | Default | Description |
|----------|---------|-------------|
| `DEBUG` | `false` | Django debug mode |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated allowed hostnames |
| `TIME_ZONE` | `America/New_York` | Timezone for schedules and timestamps |
| `REQUIRE_AUTH` | `false` | Enable password authentication |
| `APP_PASSWORD` | | Password for web UI access |

### Instance configuration

All instance settings use the `FLOWHISTORY_{PREFIX}_{FIELD}` convention. FlowHistory auto-discovers instances on startup by scanning for `_URL` (remote) or `_FLOWS_PATH` (local) env vars.

| Variable | Default | Description |
|----------|---------|-------------|
| `_FLOWS_PATH` | | Path to flows.json (local instances) |
| `_URL` | | Node-RED base URL (remote instances) |
| `_NAME` | Prefix as title | Display name |
| `_USER` | | Node-RED admin username (remote, runtime-only) |
| `_PASS` | | Node-RED admin password (remote, runtime-only) |
| `_SCHEDULE` | `daily` | Backup frequency: `hourly`, `daily`, `weekly` |
| `_TIME` | `03:00` | Time for scheduled backups |
| `_DAY` | `0` | Day of week for weekly backups (0=Monday) |
| `_MAX_BACKUPS` | `20` | Max backups before oldest are deleted |
| `_MAX_AGE_DAYS` | `30` | Max backup age in days |
| `_POLL_INTERVAL` | `60` | Remote API poll interval in seconds |
| `_WATCH` | `true` | Enable file watching (local) or API polling (remote) |
| `_DEBOUNCE` | `3` | Seconds to wait after file change before backup (local) |
| `_ALWAYS_BACKUP` | `false` | Create backup even if flows unchanged |
| `_BACKUP_CREDENTIALS` | `false` | Include flows_cred.json in backups (local) |
| `_BACKUP_SETTINGS` | `false` | Include settings.js in backups (local) |
| `_RESTART_ON_RESTORE` | `false` | Restart container after restore (local) |
| `_CONTAINER_NAME` | `nodered` | Docker container name for restart (local) |
| `_COLOR` | Auto-assigned | Hex color for UI accent (e.g., `#3B82F6`) |
| `_NOTIFY` | `true` | Enable notifications for this instance |
| `_NOTIFY_EVENTS` | *(defaults)* | Comma-separated events, `all`, `none`, or blank for defaults |
| `_DISCORD_WEBHOOK_URL` | | Per-instance Discord webhook URL (overrides global) |
| `_SLACK_WEBHOOK_URL` | | Per-instance Slack webhook URL (overrides global) |
| `_TELEGRAM_BOT_TOKEN` | | Per-instance Telegram bot token (overrides global) |
| `_TELEGRAM_CHAT_ID` | | Per-instance Telegram chat ID (overrides global) |
| `_PUSHBULLET_API_KEY` | | Per-instance Pushbullet API key (overrides global) |
| `_HOMEASSISTANT_URL` | | Per-instance Home Assistant URL (overrides global) |
| `_HOMEASSISTANT_TOKEN` | | Per-instance Home Assistant access token (overrides global) |

### Notifications

FlowHistory supports five notification backends: **Discord**, **Slack**, **Telegram**, **Pushbullet**, and **Home Assistant**. Each backend can be configured globally (applies to all instances) or per-instance (overrides the global value).

**Global** variables use the `FLOWHISTORY_NOTIFY_` prefix and apply to every instance that doesn't have its own override:

| Variable | Description |
|----------|-------------|
| `FLOWHISTORY_NOTIFY_DISCORD_WEBHOOK_URL` | Discord incoming webhook URL |
| `FLOWHISTORY_NOTIFY_SLACK_WEBHOOK_URL` | Slack incoming webhook URL |
| `FLOWHISTORY_NOTIFY_TELEGRAM_BOT_TOKEN` | Telegram bot token (both token and chat ID required) |
| `FLOWHISTORY_NOTIFY_TELEGRAM_CHAT_ID` | Telegram chat ID |
| `FLOWHISTORY_NOTIFY_PUSHBULLET_API_KEY` | Pushbullet API key |
| `FLOWHISTORY_NOTIFY_HOMEASSISTANT_URL` | Home Assistant URL (both URL and token required) |
| `FLOWHISTORY_NOTIFY_HOMEASSISTANT_TOKEN` | Home Assistant long-lived access token |

**Per-instance** overrides use the standard `FLOWHISTORY_{PREFIX}_` pattern (e.g. `FLOWHISTORY_LOCAL_DISCORD_WEBHOOK_URL`). When set, the per-instance value takes priority over the global one for that instance.

All credentials are read from the environment at runtime and never stored in the database. Multiple backends can be active simultaneously — a single event will notify all configured backends.

Default notification events: `backup_failed`, `restore_success`, `restore_failed`. Set `_NOTIFY_EVENTS=all` to receive all events, or `_NOTIFY_EVENTS=none` to silence an instance.

Env vars seed the database on first creation only. To re-apply env var values to existing instances, run:

```bash
docker exec flowhistory python manage.py discover_instances --force
```

## Architecture

Single container running three processes via entrypoint.sh:

- **gunicorn** — serves the Django web application
- **APScheduler** — runs per-instance scheduled backups and retention cleanup
- **watcher** — file watchers for local instances (inotify + polling fallback) and remote API pollers

All business logic lives in `backup/services/`. Data is stored in SQLite (WAL mode). Backups are tar.gz archives stored in per-instance subdirectories under `backups/<slug>/`.

## API Endpoints

All backup/restore endpoints are scoped to an instance by slug.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/instance/<slug>/backup/` | POST | Create a manual backup |
| `/api/instance/<slug>/backup/<id>/label/` | POST | Set backup label |
| `/api/instance/<slug>/backup/<id>/notes/` | POST | Set backup notes |
| `/api/instance/<slug>/backup/<id>/pin/` | POST | Toggle backup pin |
| `/api/instance/<slug>/bulk/` | POST | Bulk action (pin/unpin/delete) |
| `/api/instance/<slug>/restore/<id>/` | POST | Restore from a backup |
| `/api/instance/<slug>/test-connection/` | POST | Test remote connection |
| `/api/instance/<slug>/notifications/test/` | POST | Send test notification |
| `/health/` | GET | Health check |

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

## License

MIT
