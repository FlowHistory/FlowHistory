# ADR 0013: Multi-Instance Node-RED Support

## Status
Proposed

## Context

The application currently assumes a single Node-RED instance. `NodeRedConfig` is used as a singleton (`pk=1`) throughout views, services, and management commands. All backups, schedules, watchers, and retention policies operate against that one config.

Users running multiple Node-RED instances (e.g., production + development, or separate instances per automation domain) must deploy a separate flowhistory container for each. This works but means multiple dashboards, no unified view, and duplicated infrastructure.

Multi-instance support would let a single flowhistory deployment manage backups for several Node-RED instances, each with its own flows path, schedule, retention policy, and container name.

## Decision

Remove the singleton constraint on `NodeRedConfig` and make it a first-class "instance" entity. Each instance gets its own independent configuration, backup history, watcher, and scheduler job.

**Key design decisions:**
- No legacy URL redirects — clean break (app not in active use by external consumers)
- Root `/` auto-redirects to instance dashboard when only 1 instance exists
- Scheduler/watcher config changes require container restart (simple, documented)

### 1. Model Changes

**NodeRedConfig** — remove singleton assumption, add instance identity:

| Change | Detail |
|--------|--------|
| Remove `pk=1` hardcoding | All queries use config FK or URL parameter instead |
| Add `slug` field | `SlugField(max_length=100, unique=True)`, URL-safe, auto-generated from name with dedup (`-2`, `-3`) |
| Add `color` field | `CharField(max_length=7, blank=True, default="")`, hex like `#3B82F6` for visual distinction |
| Add `is_enabled` field | `BooleanField(default=True)`, master toggle to disable without deleting |
| Add `created_at` field | `DateTimeField(auto_now_add=True)`, track when instance was added |
| Add `backup_dir` property | `Path(settings.BACKUP_DIR) / self.slug` |
| Override `save()` | Auto-generate slug from name if blank, with uniqueness dedup |

**BackupRecord** and **RestoreRecord** already have a `config` ForeignKey — no schema change needed, just ensure all queries filter by config.

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
| `/api/instance/<slug>/backup/<id>/label/` | Set label for instance backup |

### 3. Dashboard Changes

**Aggregate dashboard (`/`):**
- If only 1 instance exists, auto-redirect to its instance dashboard
- Otherwise: instance cards grid — one card per instance showing name, color accent, status, last backup time, total backups
- Click instance card → instance dashboard
- "Add Instance" button
- Global stats row (total backups across all instances)

**Instance dashboard (`/instance/<slug>/`):**
- Same layout as current dashboard but scoped to one instance
- Breadcrumb: Home > Instance Name
- Instance-specific stat cards and backup history table

### 4. Service Changes

All services currently fetch `NodeRedConfig.objects.get_or_create(pk=1)`. Change to require `config` parameter explicitly (no fallback):

- `backup_service.create_backup(config, trigger)` — make `config` required, remove pk=1 fallback
- `retention_service.apply_retention(config)` — same
- `watcher_service` — `_FlowsHandler` stores `config_id` in `__init__`, fetches by that ID instead of pk=1. One handler per instance.
- `docker_service.restart_container(container_name)` — already parameterized, no changes
- `diff_service` — stateless, no changes needed
- `flow_parser` — stateless, no changes needed

### 5. Scheduler Changes

APScheduler currently runs one backup job and one retention job. Change to dynamic per-instance job management:

- On startup, loop over `NodeRedConfig.objects.filter(is_enabled=True, is_active=True)`
- Create a job pair (backup + retention) per instance
- Job IDs include config ID: `backup_{config.pk}`, `retention_{config.pk}`
- `_scheduled_backup(config_id)` / `_scheduled_retention(config_id)` accept config_id parameter
- Adding/removing instances requires container restart to take effect

### 6. Watcher Changes

Currently one watchdog Observer watches one path. Change to:

- `start_all_watchers()` — one Observer with multiple `_FlowsHandler` instances, one per enabled+watching config
- Each handler stores its own `config_id` and debounce timer, triggers backups for its config
- Adding/removing instances requires container restart

### 7. Storage

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

Restore temp directories also scoped: `backups/<slug>/_restore_tmp/` to prevent concurrent restore collisions.

### 8. JavaScript Changes

Hard-coded URL paths in `app.js` (lines 74, 102, 117, 126) must become dynamic:

- Add `<meta name="instance-api-base" content="/api/instance/{{ config.slug }}/">` to `base.html` on instance pages
- JS reads meta tag and builds URLs from the base instead of hardcoding `/api/backup/`, `/backup/`, `/diff/`, `/api/restore/`

## Alternatives Considered

- **Separate container per instance (current approach)**: Simple but duplicates infrastructure. No unified view. Each instance needs its own port, env config, and compose entry. Gets unwieldy at 3+ instances.
- **Instance as a separate Django app**: Over-engineered. The config model already has everything needed — just remove the singleton constraint.
- **Tenant-based approach with separate databases**: Overkill for a home-lab tool. SQLite handles this fine in a single DB with FK filtering.
- **Legacy URL redirects**: Considered redirect shims for old URLs but unnecessary since the app has no external API consumers.
- **Signal-based live reload for scheduler/watcher**: More complex for minimal benefit. Container restart is simple and matches current behavior.

## Consequences

**Positive:**
- Single deployment manages all Node-RED instances
- Unified dashboard for at-a-glance status across instances
- Shared infrastructure (one container, one DB, one port)
- Each instance retains independent schedules, retention, and settings
- Single-instance users see no UX change (auto-redirect)

**Negative:**
- URL structure becomes longer with slug prefix
- Watcher and scheduler become more complex (dynamic job/observer management)
- Migration required for existing single-instance deployments
- Scheduler/watcher changes require container restart
- SQLite write contention increases slightly with more instances (still fine for home-lab scale)

## Implementation Plan

### Phase 1: Model Migration — Add New Fields

**Goal**: Add `slug`, `color`, `is_enabled`, `created_at` to `NodeRedConfig`. Zero functional changes.

**Files**:
- `backup/models.py` — add fields + `save()` override for auto-slug generation + `backup_dir` property

**Migration strategy** (schema-data-schema pattern):
1. Schema migration: add fields with safe defaults (`slug` blank, `created_at` nullable)
2. Data migration: slugify existing row's name, set `is_enabled=True`, `created_at=now()`
3. Schema migration: make `slug` non-blank, `created_at` non-null

**Details**:
- `slug = SlugField(max_length=100, unique=True, blank=True)` → populated → then `blank=False`
- `color = CharField(max_length=7, blank=True, default="")` — hex like `#3B82F6`
- `is_enabled = BooleanField(default=True)`
- `created_at = DateTimeField(null=True)` → populated → then `auto_now_add=True`
- `save()` override: auto-generate slug from name with dedup (`-2`, `-3`)
- `backup_dir` property: `Path(settings.BACKUP_DIR) / self.slug`

### Phase 2: Remove pk=1 Singleton from Services

**Goal**: All services require explicit `config` parameter. No more fallback to pk=1.

**Files**:
- `backup/services/backup_service.py` — `config` required param (remove `config=None`, delete pk=1 fallback)
- `backup/services/retention_service.py` — same
- `backup/services/watcher_service.py` — `_FlowsHandler.__init__` stores `config_id`, uses it instead of pk=1. `start_watcher(config)` required param.
- `backup/management/commands/runapscheduler.py` — pass config explicitly (still fetches pk=1 here, fixed Phase 5)
- `backup/management/commands/runwatcher.py` — pass config to `start_watcher(config)`
- `backup/tests.py` — update all service calls to pass config explicitly

### Phase 3: Per-Instance Storage Directories

**Goal**: Backups stored in `backups/<slug>/` subdirectories.

**Files**:
- `backup/services/backup_service.py` — use `config.backup_dir` instead of `settings.BACKUP_DIR`
- `backup/services/restore_service.py` — use `config.backup_dir / "_restore_tmp"` for temp dir
- New: `backup/management/commands/migrate_backup_storage.py` — moves existing files into slug subdirs, updates `BackupRecord.file_path`. Idempotent.
- `entrypoint.sh` — add `python manage.py migrate_backup_storage` after migrations

### Phase 4: URL Restructuring + View Refactor

**Goal**: Instance-scoped URLs. Clean break, no legacy redirects.

**Files**:
- `backup/urls.py` — full rewrite with `instance_patterns` and `instance_api_patterns` using `include()`
- `backup/views.py`:
  - Delete `_get_or_create_config()`, replace with `_get_config_by_slug(slug)` → `get_object_or_404`
  - `dashboard(request)` → aggregate view: if 1 instance, redirect; else show cards grid
  - New `instance_dashboard(request, slug)` = current dashboard scoped by slug
  - New `instance_add(request)` = create instance form
  - All instance views gain `slug` param
  - Rename `settings_view` → `instance_settings`
- `backup/forms.py` — add `slug` to fields, `clean_slug()` rejecting reserved words (`add`, `api`)

### Phase 5: Multi-Instance Scheduler + Watcher

**Goal**: Dynamic per-instance jobs and watchers.

**Files**:
- `backup/management/commands/runapscheduler.py` — loop enabled configs, per-instance job IDs, accept `config_id` param
- `backup/services/watcher_service.py` — new `start_all_watchers()`, one Observer, multiple handlers
- `backup/management/commands/runwatcher.py` — call `start_all_watchers()`

### Phase 6: Templates + JavaScript

**Goal**: All templates use instance-scoped URLs. JS uses dynamic URL construction.

**Files**:
- `backup/templates/backup/dashboard.html` — rewrite as aggregate: instance cards grid
- New: `backup/templates/backup/instance_dashboard.html` — current dashboard adapted with slug in URLs
- New: `backup/templates/backup/instance_add.html` — add instance form
- `backup/templates/backup/base.html` — contextual nav, `<meta>` tag for API base URL
- `backup/templates/backup/settings.html` — update `{% url %}` tags with slug
- `backup/templates/backup/detail.html` — same
- `backup/templates/backup/diff.html` — same
- `backup/static/backup/js/app.js` — read meta tag, build URLs dynamically

### Phase 7: Polish + Tests

**Goal**: Delete workflow, admin registration, comprehensive multi-instance tests.

**Files**:
- `backup/views.py` — `instance_delete` view with confirmation
- `backup/urls.py` — add delete instance URL
- `backup/admin.py` — register all 3 models
- `backup/tests.py`:
  - Replace all pk=1 assumptions
  - Multi-instance isolation tests
  - Aggregate dashboard tests (auto-redirect with 1, grid with 2+)
  - Slug auto-generation + conflict resolution tests
  - Per-instance storage isolation tests

### Dependency Graph

```
Phase 1 (Model) → Phase 2 (Services) → Phase 3 (Storage) → Phase 4 (URLs/Views)
                                                                    ↓
                                                        Phase 5 (Scheduler/Watcher)  ← parallel
                                                        Phase 6 (Templates/JS)       ← parallel
                                                                    ↓
                                                            Phase 7 (Polish/Tests)
```

### Migration Strategy for Existing Deployments

1. User pulls new image, runs `docker compose up -d --build`
2. `entrypoint.sh` runs `migrate` → adds fields, populates slug for existing row
3. `entrypoint.sh` runs `migrate_backup_storage` → moves files into `backups/<slug>/`
4. Old URLs no longer exist (clean break)

### Verification

After each phase:
- `docker exec flowhistory python manage.py test backup -v2`
- `docker compose up -d --build` and test in browser

Final end-to-end:
1. Fresh deploy → verify auto-redirect to single instance dashboard
2. Add second instance via `/instance/add/`
3. Manual backup on both → verify separate `backups/<slug>/` dirs
4. Check logs: scheduler creates 2 job pairs
5. Aggregate dashboard shows both instance cards
6. Delete one instance → verify cleanup (records + files)
7. Back to 1 instance → auto-redirect works again

## Todos

- [ ] Phase 1: Add slug, color, is_enabled, created_at fields to NodeRedConfig
- [ ] Phase 1: Schema-data-schema migration (3 migrations)
- [ ] Phase 1: Add save() override for auto-slug and backup_dir property
- [ ] Phase 2: Make config required param in backup_service and retention_service
- [ ] Phase 2: Refactor watcher_service _FlowsHandler to store config_id
- [ ] Phase 2: Update management commands to pass config explicitly
- [ ] Phase 2: Update tests for explicit config passing
- [ ] Phase 3: Use config.backup_dir for backup storage paths
- [ ] Phase 3: Scope restore temp dir per instance
- [ ] Phase 3: Create migrate_backup_storage management command
- [ ] Phase 3: Update entrypoint.sh to run storage migration
- [ ] Phase 4: Rewrite URL patterns with instance scoping
- [ ] Phase 4: Refactor all views to accept slug parameter
- [ ] Phase 4: Create aggregate dashboard view with auto-redirect
- [ ] Phase 4: Create instance_add view
- [ ] Phase 4: Update forms with slug field and validation
- [ ] Phase 5: Update scheduler for dynamic multi-instance jobs
- [ ] Phase 5: Create start_all_watchers() for multi-instance observers
- [ ] Phase 6: Create aggregate dashboard template (instance cards grid)
- [ ] Phase 6: Create instance_dashboard template from current dashboard
- [ ] Phase 6: Create instance_add template
- [ ] Phase 6: Update all templates with instance-scoped URLs
- [ ] Phase 6: Update app.js to use dynamic URL construction via meta tag
- [ ] Phase 7: Create instance_delete view with confirmation
- [ ] Phase 7: Register models in admin.py
- [ ] Phase 7: Write comprehensive multi-instance tests
