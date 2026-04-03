# ADR 0006: Diff Service, Retention Service, Watcher Service & Management Commands

## Status
Implemented

## Context

With backup and restore services complete (ADRs 0004-0005), the next layer of functionality is needed: structural diffing between backups, automatic cleanup of old backups, file-change-triggered backups via watchdog, and the management commands to run the scheduler and watcher as long-lived processes alongside gunicorn.

## Decision

### 1. Diff Service (`backup/services/diff_service.py`)

Extracted from `backup_service.py` into a standalone module to enable reuse by the future diff viewer template.

**Functions:**
- `diff_tab_summaries(prev, current)` — Compare two parsed flow structures at the node level, returning tabs added/removed/modified with per-node detail
- `parse_flows_from_archive(archive_path)` — Extract and parse flows.json from a tar.gz archive
- `diff_backup_archives(archive_path_a, archive_path_b)` — Compare two archives end-to-end, returning diff plus both parsed structures

`backup_service.py` was refactored to import from `diff_service` instead of using private methods.

**Node-level diffing:** The diff service compares individual nodes by ID within each tab, not just node counts. For each modified tab, it reports:
- `nodes_added` / `nodes_removed` / `nodes_modified` — each with node type, name, and group name
- `changed_fields` — list of fields that differ on modified nodes (e.g. `func`, `wires`, `name`)
- Positional fields (`x`, `y`, `w`, `h`) are excluded from comparison so moving nodes doesn't trigger a change
- Falls back to count-only comparison for backward compatibility with older parsed data

**Flow parser enhancements** (`backup/services/flow_parser.py`):
- Tracks groups (`type: "group"`) indexed by ID with name and parent tab
- Builds `nodes_by_id` dict mapping every node (except tabs/subflows) to its type, name, parent tab (`z`), group (`g`), and `_data` (content fields minus positional fields)
- `_content_fields(node)` strips `x`, `y`, `w`, `h` for content comparison

### 2. Retention Service (`backup/services/retention_service.py`)

**Function:** `apply_retention(config=None)` — Deletes backups exceeding limits.

**Strategy:**
- Delete by age first (`max_age_days`), then by count (`max_backups`)
- Deletes both the `BackupRecord` and the archive file on disk
- Protects `trigger="pre_restore"` backups less than 24 hours old
- Called automatically after every successful backup (via `backup_service`)
- Also runs on a daily schedule (04:00) as a safety net

### 3. Watcher Service (`backup/services/watcher_service.py`)

**Design:**
- `watchdog.Observer` monitors the directory containing flows.json
- `_FlowsHandler` filters events to only flows.json modifications
- **Debouncing** via `threading.Timer` — resets on each event, fires after `watch_debounce_seconds` of quiet (configurable, default 30s)
- Re-reads `NodeRedConfig` from DB on each event for dynamic configuration (watch_enabled, debounce time)
- On debounce complete, calls `create_backup(trigger="file_change")`
- `start_watcher()` blocks until SIGINT/SIGTERM with graceful shutdown

### 4. Management Commands

**`runwatcher`** — Minimal wrapper calling `start_watcher()`.

**`runapscheduler`** — Sets up APScheduler with:
- Backup job: CronTrigger built from config (hourly/daily/weekly), re-checks `is_active` on each execution
- Retention job: Daily at 04:00
- Uses `DjangoJobStore` for persistence, `replace_existing=True` for idempotent restarts

### 5. Entrypoint Update

`entrypoint.sh` now runs all three processes:
1. `runapscheduler` (background)
2. `runwatcher` (background)
3. `gunicorn` (background)

All run as background jobs with `trap` forwarding SIGTERM/SIGINT for clean Docker shutdown. The shell script `wait`s for all processes.

### 6. Dependency Addition

Added `watchdog>=4.0.0` to `pyproject.toml`.

## Consequences

**Positive:**
- Backups trigger automatically on flows.json changes with configurable debounce
- Old backups are cleaned up automatically by count and age
- Diff logic is reusable for the upcoming diff viewer UI
- All three processes managed cleanly in a single container
- Dynamic config re-read means no restart needed for most setting changes

**Negative:**
- Schedule frequency/time changes require container restart (cron trigger is set at startup)
- Three background processes in one container increase failure surface (mitigated by Docker restart policy)
