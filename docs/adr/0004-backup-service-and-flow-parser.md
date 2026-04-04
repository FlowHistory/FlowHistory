# ADR 0004: Backup Service and Flow Parser

## Status
Implemented

## Context

With the Django app bootstrapped (ADR 0002) and containerized (ADR 0003), the application serves a dashboard but cannot actually create backups — the manual backup endpoint returns a 501 stub. The core backup pipeline needs to be implemented: creating tar.gz archives of Node-RED files, computing checksums for deduplication, and parsing flows.json to provide tab-level change summaries on the dashboard.

This ADR covers the first two services from the architecture plan (ADR 0001): `backup_service.py` and `flow_parser.py`, plus wiring the manual backup API endpoint.

## Decision

### 1. Flow Parser (`backup/services/flow_parser.py`)

Parses the Node-RED flows.json (a flat JSON array of node objects) into a structured summary:

- **Tabs**: Nodes with `type: "tab"` — identified by `id`, labeled by `label`
- **Node grouping**: Other nodes grouped by their `z` field (parent tab ID)
- **Subflows**: Nodes with `type: "subflow"` — tracked separately
- **Config/global nodes**: Nodes with no `z` field are config nodes; others with unknown parents are global

Returns a dict with: `tabs` (sorted by label), `subflows` (sorted by name), `config_nodes` count, `global_nodes` count, `total_nodes` count.

Provides `get_tab_names()` convenience function for populating `BackupRecord.tab_summary`.

### 2. Backup Service (`backup/services/backup_service.py`)

`create_backup(config, trigger)` is the main entry point:

1. **Validate** that flows.json exists at the configured path
2. **Compute SHA256** of flows.json content
3. **Deduplicate**: For scheduled/file_change triggers, skip if checksum matches the last successful backup. Manual and pre_restore backups always proceed.
4. **Create tar.gz** archive containing:
   - `flows.json` (always, from in-memory bytes to avoid TOCTOU)
   - `flows_cred.json` (if config.backup_credentials and file exists)
   - `settings.js` (if config.backup_settings and file exists)
5. **Parse tab summary** from current flows.json
6. **Compute changes** vs previous backup by extracting flows.json from the last archive and diffing parsed structures (tabs added/removed/modified by node count)
7. **Save BackupRecord** with all metadata
8. **Update config** `last_successful_backup` timestamp and clear error state

**Naming convention**: `flowhistory_{YYYYMMDD}_{HHMMSS}_{8-char-uuid}.tar.gz`

**Return values**: Returns a `BackupRecord` on success or failure, or `None` when dedup skips. Failed backups have `status="failed"` with `error_message` populated and `config.last_backup_error` updated.

### 3. Change Detection

The diff compares two parsed flow structures at the tab level:
- Tabs added (present in current, absent in previous)
- Tabs removed (absent in current, present in previous)
- Tabs modified (same tab ID, different node count)

Stored in `BackupRecord.changes_summary` as a JSON dict for fast dashboard rendering without re-parsing archives.

### 4. API Endpoint

The existing stub `api_create_backup` view now calls `create_backup(trigger="manual")` and returns:
- `200 {"status": "success", "backup": {...}}` on success
- `200 {"status": "skipped", ...}` if dedup skipped (shouldn't happen for manual, but safe)
- `500 {"status": "error", ...}` on known failure (returned from `create_backup` with `status="failed"`)
- `500 {"status": "error", ...}` on unexpected exception

## Consequences

**Positive:**
- The "Create Backup" button on the dashboard now works end-to-end
- Tab-level change tracking provides useful context without expensive deep diffing
- Checksum dedup prevents wasted disk space from identical scheduled/watcher backups
- Archive format (tar.gz with flat file names) is simple to extract manually if needed

**Negative:**
- Change detection compares node counts per tab, not node content — a renamed node won't show as a modification if the count stays the same. This is an intentional trade-off for performance on 51K-line files.
- Extracting the previous archive for each backup adds I/O. At ~150 KB per archive this is negligible, but could be optimized with a cached parsed state if needed.
