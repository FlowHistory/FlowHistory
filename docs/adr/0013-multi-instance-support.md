# ADR 0013: Multi-Instance Node-RED Support

## Status
Implemented

## Context

The application currently assumes a single Node-RED instance. `NodeRedConfig` is used as a singleton (`pk=1`) throughout views, services, and management commands. All backups, schedules, watchers, and retention policies operate against that one config.

Users may run multiple Node-RED instances across different servers or on the same host. Currently they must deploy a separate FlowHistory container for each, which means multiple dashboards, duplicated infrastructure, and no unified view.

Multi-instance support lets a single FlowHistory deployment manage backups for several Node-RED instances. Each instance has its own independent configuration, backup history, change detection, scheduler, and retention policy.

### Two Source Types

Instances can be **local** (file-based) or **remote** (API-based):

- **Local**: FlowHistory watches a `flows.json` file on a mounted volume (current behavior). Best for Node-RED running on the same Docker host.
- **Remote**: FlowHistory polls the Node-RED Admin API (`GET /flows`) over HTTP. Works across the network — Node-RED can be on any server.

Both source types feed into the same backup pipeline. The only difference is how changes are detected and how flow data is retrieved.

### Configuration and Credential Storage

Per ADR 0021 (Accepted), all instance config can be defined via environment variables using the `FLOWHISTORY_{PREFIX}_{FIELD}` convention. FlowHistory auto-discovers instances on startup by scanning for `FLOWHISTORY_*_URL` (remote) and `FLOWHISTORY_*_FLOWS_PATH` (local) env vars. Env vars seed the database on first creation; UI edits take precedence after that. Credentials (`_USER`, `_PASS`) are always read from env at runtime, never stored in the database. See ADR 0021 for full rationale and env var reference.

## Decision

Remove the singleton constraint on `NodeRedConfig` and make it a first-class "instance" entity. Each instance gets its own independent configuration, backup history, change detection method, and scheduler job.

**Key design decisions:**
- No legacy URL redirects — clean break (app not in active use by external consumers)
- Root `/` auto-redirects to instance dashboard when only 1 instance exists
- Scheduler/watcher config changes require container restart (simple, documented)
- Credentials in env vars, everything else in DB (ADR 0021)

### 1. Model Changes

**NodeRedConfig** — remove singleton assumption, add instance identity and remote support:

#### New Fields

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `name` | `CharField(max_length=100)` | `"Node-RED"` | Display name shown in UI |
| `slug` | `SlugField(max_length=100, unique=True)` | Auto-generated from name | URL-safe identifier, auto-deduped (`-2`, `-3`) |
| `color` | `CharField(max_length=7, blank=True, default="")` | `""` | Hex color like `#3B82F6` for UI distinction |
| `is_enabled` | `BooleanField` | `True` | Master toggle — disabled instances are ignored by scheduler/watcher |
| `created_at` | `DateTimeField(auto_now_add=True)` | Now | Track when instance was added |
| `source_type` | `CharField(max_length=10, choices)` | `"local"` | `"local"` (file watch) or `"remote"` (API poll) |
| `nodered_url` | `URLField(blank=True, default="")` | `""` | Node-RED base URL for remote instances (e.g., `http://192.168.1.50:1880`) |
| `env_prefix` | `CharField(max_length=50, blank=True, default="")` | `""` | Env var prefix (e.g., `"PROD"` → reads `FLOWHISTORY_PROD_*` vars). Set automatically for env-discovered instances. |
| `poll_interval_seconds` | `PositiveIntegerField` | `60` | How often to poll the remote API for changes (remote only) |

#### Existing Fields (Retained Per-Instance)

These already exist and remain per-instance — each instance has its own independent values:

| Field | Purpose |
|-------|---------|
| `flows_path` | Path to flows.json (local instances only, no default — must be configured) |
| `backup_frequency` | Schedule frequency: hourly, daily, weekly |
| `backup_time` | Time of day for scheduled backups |
| `backup_day` | Day of week for weekly backups |
| `max_backups` | Maximum backup count before retention deletes oldest |
| `max_age_days` | Maximum backup age before retention deletes |
| `schedule_enabled` | Whether scheduled backups are enabled (renamed from `is_active` for clarity) |
| `always_backup` | Create backup even if flows unchanged |
| `watch_enabled` | Enable file watching (local) or API polling (remote) |
| `watch_debounce_seconds` | Debounce delay after change detection (local only) |
| `backup_credentials` | Include credentials file in backup archives |
| `backup_settings` | Include settings file in backup archives |
| `restart_on_restore` | Restart Node-RED container after restore |
| `nodered_container_name` | Docker container name for restart |
| `last_successful_backup` | Timestamp of last successful backup |
| `last_backup_error` | Error message from last failed backup |

#### Model Methods

```python
INSTANCE_COLORS = ["#3B82F6", "#EF4444", "#10B981", "#F59E0B", "#8B5CF6", "#EC4899"]

@property
def backup_dir(self):
    """Per-instance backup storage directory."""
    return Path(settings.BACKUP_DIR) / self.slug

def get_nodered_credentials(self):
    """Read credentials from environment variables using configured prefix."""
    if not self.env_prefix:
        return None, None
    prefix = self.env_prefix.upper()
    username = os.environ.get(f"FLOWHISTORY_{prefix}_USER", "")
    password = os.environ.get(f"FLOWHISTORY_{prefix}_PASS", "")
    return username, password

RESERVED_SLUGS = {"add", "api"}

def save(self, *args, **kwargs):
    """Auto-generate slug from name with uniqueness dedup."""
    if not self.slug:
        base = slugify(self.name) or "instance"
        if base in self.RESERVED_SLUGS:
            base = f"{base}-instance"
        slug, n = base, 1
        while NodeRedConfig.objects.filter(slug=slug).exclude(pk=self.pk).exists():
            n += 1
            slug = f"{base}-{n}"
        self.slug = slug
    if not self.color:
        idx = NodeRedConfig.objects.exclude(pk=self.pk).count() % len(self.INSTANCE_COLORS)
        self.color = self.INSTANCE_COLORS[idx]
    super().save(*args, **kwargs)
```

#### Field Applicability by Source Type

Some fields only apply to one source type. The settings UI should show/hide them accordingly.

| Field | Local | Remote | Notes |
|-------|-------|--------|-------|
| `flows_path` | Yes | No | Local file path |
| `nodered_url` | No | Yes | Remote API URL |
| `env_prefix` | Yes | Yes | Maps to `FLOWHISTORY_{PREFIX}_*` env vars |
| `poll_interval_seconds` | No | Yes | Remote polling frequency |
| `watch_enabled` | Yes | Yes | File watch (local) or API poll (remote) |
| `watch_debounce_seconds` | Yes | No | Local debounce only — remote uses `poll_interval_seconds` |
| `backup_credentials` | Yes | No | Remote API returns flows only, not credential files |
| `backup_settings` | Yes | No | Remote API doesn't serve settings files |
| `restart_on_restore` | Yes | No | Remote restart not supported — out of scope for initial implementation |
| `nodered_container_name` | Yes | No | Same as above |

**BackupRecord** and **RestoreRecord** already have a `config` ForeignKey — no schema change needed.

### 2. URL Structure

Instance-scoped URLs with slug prefix. Root dashboard is aggregate (auto-redirects if only 1 instance).

| URL | Description |
|-----|-------------|
| `/` | Aggregate dashboard — instance cards grid; auto-redirect if only 1 instance |
| `/instance/add/` | Add new instance form |
| `/instance/<slug>/` | Instance dashboard — backup history for this instance |
| `/instance/<slug>/settings/` | Instance settings form |
| `/instance/<slug>/backup/<id>/` | Backup detail (scoped to instance) |
| `/instance/<slug>/backup/<id>/download/` | Download backup |
| `/instance/<slug>/backup/<id>/delete/` | Delete backup |
| `/instance/<slug>/diff/<id>/` | Diff viewer |
| `/instance/<slug>/diff/<id>/<compare_id>/` | Diff compare |
| `/instance/<slug>/delete/` | Delete instance (with confirmation) |
| `/api/instance/<slug>/backup/` | Create manual backup for instance |
| `/api/instance/<slug>/restore/<id>/` | Restore for instance |
| `/api/instance/<slug>/backup/<id>/label/` | Set label |
| `/api/instance/<slug>/backup/<id>/notes/` | Set notes |
| `/api/instance/<slug>/backup/<id>/pin/` | Toggle pin |
| `/api/instance/<slug>/bulk/` | Bulk action |
| `/api/instance/<slug>/test-connection/` | Test remote connection (remote instances only) |

### 3. Dashboard Changes

#### Aggregate Dashboard (`/`)

- If only 1 instance exists → auto-redirect to its instance dashboard
- Otherwise: instance cards grid — one card per instance showing:
  - Instance name with color accent
  - Source type badge (Local / Remote)
  - Status indicator (healthy / error / disabled)
  - Last backup time (relative)
  - Total backup count
  - Next scheduled backup time
  - Storage used (sum of backup file sizes)
- Click instance card → instance dashboard
- "Add Instance" button
- Global stats row: total backups, total storage, instances active/total

#### Instance Dashboard (`/instance/<slug>/`)

Same layout as current dashboard but scoped to one instance:
- Breadcrumb: Home > Instance Name
- Instance-specific stat cards (name, status, backup count, last backup)
- Backup history table with all current columns
- Instance color accent in header/breadcrumb

#### Backup History Table Enhancements

When viewing from the aggregate dashboard or any cross-instance view, backups show which instance they belong to:
- Instance name column with color dot
- Filterable by instance

### 4. Settings Page Changes

The instance settings page (`/instance/<slug>/settings/`) adapts based on `source_type`:

#### Common Settings (Always Shown)
- **Instance Name** — display name
- **Source Type** — Local / Remote toggle (changes which fields are visible)
- **Color** — hex color picker for UI distinction

#### Local-Only Settings
- **Flows Path** — absolute path to `flows.json`
- **File Watching** — enable/disable, debounce seconds
- **Backup Contents** — include credentials file, include settings file

#### Remote-Only Settings
- **Node-RED URL** — base URL (e.g., `http://192.168.1.50:1880`). Read-only if seeded from env.
- **Env Prefix** — prefix for env vars (e.g., `PROD`). Read-only if auto-discovered.
  - Help text: "Set `FLOWHISTORY_PROD_USER` and `FLOWHISTORY_PROD_PASS` in your `.env` file, then restart the container"
  - Show warning if expected env vars (`_USER`, `_PASS`) are not set
  - For UI-created instances: show inline setup instructions after the user enters a prefix
- **Poll Interval** — seconds between API polls
- **Connection Test** button — validates URL + credentials, shows success/error inline

#### Watch/Poll Settings (Adapted by Source Type)
- **File Watching** (local) / **API Polling** (remote) — enable/disable toggle. Help text changes based on source type: "Enable file watching for automatic backups on change" (local) or "Enable API polling to detect remote flow changes" (remote).

#### Common Settings (Always Shown)
- **Schedule** — frequency, time, day, schedule enabled toggle, always-backup toggle
- **Retention** — max backups, max age days
- **Restore** — restart on restore toggle, container name

### 5. Change Detection

#### Local Instances (File Watching)

Same as current behavior:
- Watchdog `Observer` monitors `flows_path` directory
- `_FlowsHandler` detects modifications to the flows file
- Debounce timer prevents duplicate backups from rapid saves
- Falls back to polling if inotify is unavailable

Changes from current:
- `_FlowsHandler.__init__` stores `config_id`, fetches config by that ID (not pk=1)
- `start_all_watchers()` creates one handler per enabled local instance
- One Observer with multiple handlers, each watching different directories

#### Remote Instances (API Polling)

New behavior:
- Periodic HTTP poll to `GET {nodered_url}/flows`
- Authenticate if `env_prefix` is set:
  1. `POST {nodered_url}/auth/token` with username/password → Bearer token
  2. Cache token, refresh on 401 (tokens expire after 7 days by default)
- Checksum the response body
- If checksum differs from last backup → trigger `create_backup(config, trigger="file_change")`
- Store the fetched flows JSON as the backup (write to temp file, then archive)

Implementation:
- New `RemotePoller` class in `watcher_service.py` (or separate `remote_service.py`)
- Stores `config_id`, `last_checksum`, `auth_token`, `token_expires`, `consecutive_failures`
- Runs on a configurable interval (`poll_interval_seconds`, default 60s)
- Error handling: log failures, update `last_backup_error`, continue polling
- Connection errors don't crash the poller — retry on next interval
- Error backoff: after 3 consecutive failures, log at WARNING instead of ERROR and double the poll interval (capped at 5 minutes). Reset interval and failure count on success.

### 6. Backup Service Changes

`create_backup(config, trigger)` — make `config` a required parameter (remove fallback to pk=1).

For **local** instances (current behavior):
- Read flows from `config.flows_path`
- Optionally include credentials and settings files
- Archive to `config.backup_dir / filename`

For **remote** instances:
- Flows data already fetched by the poller (passed as argument or written to temp file)
- `create_backup(config, trigger, flows_data=None)` — if `flows_data` is provided, use it instead of reading from disk
- No credentials/settings files available from remote API — only flows are backed up
- Archive to `config.backup_dir / filename`

### 7. Restore Service Changes

For **local** instances (current behavior):
- Extract archive to `config.flows_path` directory
- Optionally restart container via Docker socket

For **remote** instances:
- Extract archive to temp directory
- `POST {nodered_url}/flows` with the flows JSON to deploy
- Requires `flows.write` permission on the Node-RED admin API
- Optionally restart via `POST {nodered_url}/flows` with `deployment` type
- Note: Remote restore may not support credential/settings file restoration

### 8. Scheduler Changes

APScheduler creates per-instance jobs on startup:
- Loop over `NodeRedConfig.objects.filter(is_enabled=True, schedule_enabled=True)`
- Create a job pair per instance:
  - `backup_{config.pk}` — scheduled backup
  - `retention_{config.pk}` — scheduled retention
- `_scheduled_backup(config_id)` / `_scheduled_retention(config_id)` accept config_id
- Adding/removing instances requires container restart

### 9. Storage

Backup archives organized by instance slug:

```
backups/
├── nodered-prod/
│   ├── flowhistory_20260401_030000_abc12345.tar.gz
│   └── ...
├── nodered-dev/
│   ├── flowhistory_20260401_030000_def67890.tar.gz
│   └── ...
```

Restore temp directories also scoped: `backups/<slug>/_restore_tmp/`

### 10. JavaScript Changes

Hard-coded URL paths in `app.js` must become dynamic:
- Add `<meta name="instance-api-base" content="/api/instance/{{ config.slug }}/">` to `base.html` on instance pages
- JS reads meta tag and builds URLs from the base

### 11. Environment Variables and Auto-Discovery

Instances can be fully defined via env vars using the `FLOWHISTORY_{PREFIX}_{FIELD}` convention. On startup, FlowHistory scans for these and auto-creates `NodeRedConfig` rows for new prefixes.

```bash
# .env — remote instance (auto-discovered via _URL)
FLOWHISTORY_PROD_URL=http://192.168.1.50:1880
FLOWHISTORY_PROD_USER=admin
FLOWHISTORY_PROD_PASS=secretpass1
FLOWHISTORY_PROD_NAME=Production
FLOWHISTORY_PROD_SCHEDULE=daily
FLOWHISTORY_PROD_TIME=03:00
FLOWHISTORY_PROD_MAX_BACKUPS=30

# .env — local instance (auto-discovered via _FLOWS_PATH)
FLOWHISTORY_LOCAL_FLOWS_PATH=/nodered-data/flows.json
FLOWHISTORY_LOCAL_NAME=Docker Host
```

**Discovery logic:**
- `FLOWHISTORY_*_URL` → remote instance
- `FLOWHISTORY_*_FLOWS_PATH` (without matching `_URL`) → local instance
- Env vars seed on first creation only — UI edits take precedence after that
- Credentials (`_USER`, `_PASS`) are always read from env at runtime, never stored in DB

**No implicit defaults:** Unlike the current behavior where `flows_path` defaults to `/nodered-data/flows.json`, multi-instance requires explicit configuration. At least one `FLOWHISTORY_*_URL` or `FLOWHISTORY_*_FLOWS_PATH` env var must be set, or instances must be created via the UI. If no instances are configured, the app shows the aggregate dashboard with an "Add Instance" prompt.

See ADR 0021 for the full env var reference table and deployment examples.

## Alternatives Considered

- **Separate container per instance (current approach)**: Simple but duplicates infrastructure. No unified view.
- **YAML config file for instances**: Split-brain with config in file + DB. See ADR 0021 for full evaluation.
- **Credentials in database**: Security concern — plaintext in SQLite. See ADR 0021.
- **Signal-based live reload for scheduler/watcher**: More complex for minimal benefit. Container restart is simple.
- **WebSocket/SSE for real-time remote change detection**: Node-RED doesn't expose this. Polling is the only option for remote.

## Consequences

### Positive
- Single deployment manages all Node-RED instances (local and remote)
- Unified dashboard for at-a-glance status across instances
- Each instance retains fully independent config: schedule, retention, contents, change detection
- Remote support eliminates the requirement for shared file access
- Single-instance users see no UX change (auto-redirect)
- Credentials never stored in database

### Negative
- URL structure becomes longer with slug prefix
- Watcher and scheduler become more complex (multi-handler, multi-job)
- Container restart needed for instance add/remove
- Remote instances can only back up flows (not credentials/settings files)
- Remote restore depends on Node-RED Admin API write permissions
- Polling remote instances adds network traffic (mitigated by configurable interval)

## Implementation Plan

### Phase 1: Model Migration — Add New Fields

**Goal**: Add `name`, `slug`, `color`, `is_enabled`, `source_type`, `nodered_url`, `env_prefix`, `poll_interval_seconds`, `created_at` to `NodeRedConfig`. Zero functional changes.

**Files**:
- `backup/models.py` — add fields + `save()` override for auto-slug + `backup_dir` property + `get_nodered_credentials()`
- New: `backup/services/discovery_service.py` — `discover_instances_from_env()` scans `FLOWHISTORY_*_URL` and `FLOWHISTORY_*_FLOWS_PATH`, auto-creates `NodeRedConfig` rows for new prefixes. Supports `--force` flag to re-apply env var values to existing instances (except credentials, which are always runtime).
- `entrypoint.sh` — call `python manage.py discover_instances` after migrations
- `config/settings.py` — remove `NODERED_DATA_PATH` (replaced by `FLOWHISTORY_*_FLOWS_PATH`)

**Migration strategy** (schema-data-schema pattern):
1. Schema migration: add new fields with safe defaults (`slug` blank, `created_at` nullable, others with defaults). Rename `is_active` → `schedule_enabled` via `RenameField`.
2. Data migration: slugify existing row, set `is_enabled=True`, `source_type="local"`, `created_at=now()`
3. Schema migration: make `slug` non-blank, `created_at` non-null

### Phase 2: Remove pk=1 Singleton from Services

**Goal**: All services require explicit `config` parameter. No more fallback to pk=1.

**Files**:
- `backup/services/backup_service.py` — `config` required param
- `backup/services/retention_service.py` — same
- `backup/services/watcher_service.py` — `_FlowsHandler.__init__` stores `config_id`
- `backup/management/commands/runapscheduler.py` — pass config explicitly
- `backup/management/commands/runwatcher.py` — pass config to `start_watcher(config)`
- `backup/management/commands/checkintegrity.py` — works as-is (reads `file_path` from DB, no hardcoded paths), but should log which instance each orphan belongs to
- `backup/tests.py` — update all service calls

### Phase 3: Per-Instance Storage Directories

**Goal**: Backups stored in `backups/<slug>/` subdirectories. Existing archives migrated from root `backups/`.

**Files**:
- `backup/services/backup_service.py` — use `config.backup_dir` instead of `settings.BACKUP_DIR`
- `backup/services/restore_service.py` — use `config.backup_dir / "_restore_tmp"`
- New: `backup/management/commands/migrate_backup_storage.py`:
  - Finds all `.tar.gz` files in root `backups/` (not already in subdirs)
  - For each file, looks up the matching `BackupRecord` by `filename`
  - Gets the record's `config.slug` → moves file to `backups/<slug>/`
  - Updates `BackupRecord.file_path` to the new absolute path
  - Orphaned archives (no matching record) → `backups/_orphaned/`
  - Idempotent — safe to run multiple times
- `entrypoint.sh` — add storage migration after DB migrations and discovery

### Phase 4: URL Restructuring + View Refactor

**Goal**: Instance-scoped URLs. Clean break, no legacy redirects.

**Files**:
- `backup/urls.py` — full rewrite with instance-scoped patterns
- `backup/views.py` — aggregate dashboard, instance views with slug param
- `backup/forms.py` — add new fields, conditional field visibility by source_type

### Phase 5: Multi-Instance Scheduler + Watcher + Remote Poller

**Goal**: Per-instance jobs, multi-watcher, remote API polling.

**Files**:
- `backup/management/commands/runapscheduler.py` — per-instance job pairs
- `backup/services/watcher_service.py` — `start_all_watchers()`, multiple handlers
- New: `backup/services/remote_service.py` — `RemotePoller` class, auth token management, API polling
- `backup/management/commands/runwatcher.py` — call `start_all_watchers()`

### Phase 6: Templates + JavaScript

**Goal**: All templates use instance-scoped URLs. JS uses dynamic URL construction.

**Files**:
- `backup/templates/backup/dashboard.html` — rewrite as aggregate: instance cards grid
- New: `backup/templates/backup/instance_dashboard.html` — current dashboard adapted
- New: `backup/templates/backup/instance_add.html` — add instance form
- `backup/templates/backup/base.html` — contextual nav, `<meta>` for API base
- `backup/templates/backup/settings.html` — conditional fields by source_type
- `backup/templates/backup/detail.html` — instance-scoped URLs
- `backup/templates/backup/diff.html` — same
- `backup/static/backup/js/app.js` — dynamic URL construction via meta tag

### Phase 7: Polish + Tests

**Goal**: Delete workflow, deployment config, comprehensive multi-instance tests.

**Instance delete behavior:**
- Confirmation page shows instance name, backup count, and total storage used
- Checkbox: "Also delete backup files from disk" (default unchecked)
- If unchecked: deletes `NodeRedConfig` + all `BackupRecord`/`RestoreRecord` rows, leaves files on disk
- If checked: also deletes `backups/<slug>/` directory
- Container restart needed after deletion for scheduler/watcher cleanup

**Files**:
- `backup/views.py` — `instance_delete` view with confirmation
- `backup/admin.py` — register all models
- `docker-compose.yml` — update with `FLOWHISTORY_LOCAL_FLOWS_PATH` env var, remove `NODERED_DATA_PATH` references
- `.env.example` — replace `NODERED_DATA_PATH` with `FLOWHISTORY_*` convention, add examples for local and remote instances
- `backup/tests.py`:
  - Multi-instance isolation tests
  - Aggregate dashboard tests (auto-redirect with 1, grid with 2+)
  - Slug auto-generation + conflict resolution tests
  - Per-instance storage isolation tests
  - Remote poller tests (mocked HTTP)
  - Connection test endpoint tests
  - Conditional field visibility tests

### Dependency Graph

```
Phase 1 (Model) → Phase 2 (Services) → Phase 3 (Storage) → Phase 4 (URLs/Views)
                                                                    ↓
                                                        Phase 5 (Scheduler/Watcher/Remote)  ← overlap OK
                                                        Phase 6 (Templates/JS)              ← overlap OK
                                                                    ↓
                                                            Phase 7 (Polish/Tests)
```

**Note:** Phases 5 and 6 can overlap but aren't fully independent — Phase 6 templates need Phase 5's remote-specific views (e.g., connection test button endpoint) to be defined.

### Migration Strategy for Existing Data

A one-time throwaway migration script handles the current single-instance data. This script is **not** part of the production codebase — it runs once before the multi-instance upgrade and is deleted after.

**One-time script (`migrate_to_multi_instance.py`):**

Run manually before deploying the multi-instance version:

1. Populate new fields on the existing `NodeRedConfig` row (pk=1):
   - `name` = `"Node-RED"`, `slug` = `"node-red"`, `source_type` = `"local"`
   - `is_enabled` = `True`, `schedule_enabled` = existing `is_active` value
   - `created_at` = `now()`, `color` = `"#3B82F6"`
   - `env_prefix` = `"LOCAL"`
2. Create `backups/node-red/` directory
3. Move all `.tar.gz` files from root `backups/` into `backups/node-red/`
4. Update `BackupRecord.file_path` for each moved file
5. `BackupRecord`/`RestoreRecord` FK references stay on pk=1 — no changes needed

After running: add `FLOWHISTORY_LOCAL_FLOWS_PATH=/nodered-data/flows.json` to `.env`, deploy the new version. Discovery sees `env_prefix="LOCAL"` already exists, skips creation.

The discovery service has no legacy handling — it only creates new instances for prefixes not yet in the DB.

### Verification

After each phase:
- `docker exec flowhistory python manage.py test backup -v2`
- `docker compose up -d --build` and test in browser

Existing data migration (one-time script):
1. Run `migrate_to_multi_instance.py` against current DB + backups dir
2. Verify: `NodeRedConfig` row has slug, env_prefix, color populated
3. Verify: all archives moved to `backups/node-red/`
4. Verify: `BackupRecord.file_path` updated to new paths
5. Add `FLOWHISTORY_LOCAL_FLOWS_PATH` to `.env`, deploy new version
6. Verify: dashboard shows all existing backup history, download/restore/diff still work

Final end-to-end:
1. Fresh deploy → verify auto-redirect to single instance dashboard
2. Add second instance (local) via `/instance/add/`
3. Add third instance (remote) via `/instance/add/`
4. Manual backup on all → verify separate `backups/<slug>/` dirs
5. Verify remote instance polls and creates backups on change
6. Connection test button works for remote instance
7. Aggregate dashboard shows all instance cards with color accents
8. Each instance has independent schedule, retention, and settings
9. Delete one instance → verify cleanup (records + files)
10. Back to 1 instance → auto-redirect works again

## Todos

- [ ] Phase 1: Add name, slug, color, is_enabled, source_type, nodered_url, env_prefix, poll_interval_seconds, created_at
- [ ] Phase 1: RenameField is_active → schedule_enabled
- [ ] Phase 1: Schema-data-schema migration (3 migrations)
- [ ] Phase 1: Add save() override, backup_dir property, get_nodered_credentials()
- [ ] Phase 1: Create discovery_service.py with discover_instances_from_env()
- [ ] Phase 1: Create discover_instances management command (with --force flag)
- [ ] Phase 1: Update entrypoint.sh to run discovery after migrations
- [ ] Phase 1: Remove NODERED_DATA_PATH from settings.py
- [ ] Phase 1: One-time migration script (migrate_to_multi_instance.py) — run before deploy, delete after
- [ ] Phase 2: Make config required param in backup_service and retention_service
- [ ] Phase 2: Refactor watcher_service _FlowsHandler to store config_id
- [ ] Phase 2: Update management commands to pass config explicitly
- [ ] Phase 2: Update tests for explicit config passing
- [ ] Phase 3: Use config.backup_dir for backup storage paths
- [ ] Phase 3: Scope restore temp dir per instance
- [ ] Phase 4: Rewrite URL patterns with instance scoping
- [ ] Phase 4: Refactor all views to accept slug parameter
- [ ] Phase 4: Create aggregate dashboard view with auto-redirect
- [ ] Phase 4: Create instance_add view
- [ ] Phase 4: Update forms with conditional fields by source_type
- [ ] Phase 5: Update scheduler for dynamic multi-instance jobs
- [ ] Phase 5: Create start_all_watchers() for multi-instance observers
- [ ] Phase 5: Create RemotePoller with auth token management
- [ ] Phase 5: Create remote_service.py for API polling
- [ ] Phase 6: Create aggregate dashboard template (instance cards grid)
- [ ] Phase 6: Create instance_dashboard template from current dashboard
- [ ] Phase 6: Create instance_add template
- [ ] Phase 6: Update settings template with conditional source_type fields
- [ ] Phase 6: Update all templates with instance-scoped URLs
- [ ] Phase 6: Update app.js to use dynamic URL construction via meta tag
- [ ] Phase 4: Add /api/instance/<slug>/test-connection/ endpoint for remote instances
- [ ] Phase 7: Create instance_delete view with confirmation + optional file deletion
- [ ] Phase 7: Register models in admin.py
- [ ] Phase 7: Update docker-compose.yml and .env.example with FLOWHISTORY_* convention
- [ ] Phase 7: Write comprehensive multi-instance tests
- [ ] Phase 7: Write remote poller tests with mocked HTTP
