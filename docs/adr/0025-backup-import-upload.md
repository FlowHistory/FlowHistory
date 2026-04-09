# ADR 0025: Backup Import/Upload

## Status
Implemented

## Context

FlowHistory creates `.tar.gz` backup archives of Node-RED flows and stores them as `BackupRecord` entries. Currently, all backups originate from one of four triggers: `manual`, `scheduled`, `file_change`, or `pre_restore`. All of these read flows from the configured `flows_path` (local) or the Node-RED HTTP API (remote).

Users want to upload backup archives from **other FlowHistory instances** or from **local machines** and register them as backup records in a target instance. Use cases include:

- Migrating flows between Node-RED instances managed by separate FlowHistory deployments
- Importing a backup archive received from a colleague or downloaded from another server
- Re-importing an archive that was previously downloaded via the existing "Download" feature

The existing `restore_service.py` already validates archive contents (whitelist of filenames, symlink/hardlink rejection). The `backup_service.py` handles archive creation, checksum computation, flow parsing, and diff computation. The import feature needs to combine validation logic from both services with a new file-upload ingestion path.

### Design Constraints

- No Django forms are used anywhere in the project. All interactions use JavaScript `fetch()` to JSON API endpoints.
- File upload requires `multipart/form-data`, which is a departure from the JSON-body pattern used by every other API endpoint. This is unavoidable -- `fetch()` with `FormData` handles this natively.
- Archive naming follows the existing `flowhistory_YYYYMMDD_HHMMSS_<8-char-uuid>.tar.gz` convention.
- Security is critical: uploaded files are untrusted. Validation must reject anything beyond the three known filenames (`flows.json`, `flows_cred.json`, `settings.js`).

## Decision

### 1. API Endpoint

```
POST /api/instance/<slug>/import/
```

**Content type:** `multipart/form-data` (not JSON -- file uploads require this).

**Request fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `archive` | File | Yes | The `.tar.gz` backup archive |
| `label` | String | No | Optional label for the imported backup (max 200 chars) |
| `notes` | String | No | Optional notes for the imported backup |

**Success response** (200):
```json
{
  "status": "success",
  "message": "Backup imported successfully",
  "backup": {
    "id": 42,
    "filename": "flowhistory_20260407_143022_a1b2c3d4.tar.gz",
    "file_size": 2048,
    "checksum": "abc123...",
    "trigger": "import",
    "created_at": "2026-04-07T14:30:22+00:00",
    "tab_summary": ["Home Automation", "API Endpoints"],
    "includes_credentials": false,
    "includes_settings": false,
    "duplicate_warning": null
  }
}
```

When the `flows.json` checksum matches an existing backup for this instance, the response still succeeds but includes:
```json
"duplicate_warning": "Checksum matches existing backup 'flowhistory_20260401_...' from 2026-04-01 08:15"
```

**Error response** (400/413/500):
```json
{"status": "error", "message": "Human-readable error description"}
```

### 2. Validation

Validation is performed in a dedicated `validate_import_archive(uploaded_file)` function in a new `backup/services/import_service.py`. Validation runs **before** writing anything to disk.

| Step | Check | Error |
|------|-------|-------|
| 1 | File size <= `IMPORT_MAX_SIZE` (default 50 MB) | "Archive exceeds maximum size of 50 MB" |
| 2 | Filename ends with `.tar.gz` or `.tgz` (case-insensitive) | "File must be a .tar.gz or .tgz archive" |
| 3 | File is a valid gzip-compressed tar archive | "File is not a valid tar.gz archive" |
| 4 | Archive contains `flows.json` | "Archive must contain flows.json" |
| 5 | No members outside the whitelist `{"flows.json", "flows_cred.json", "settings.js"}` | "Archive contains unexpected files: {names}" |
| 6 | No symlinks or hardlinks in any member | "Archive contains symbolic or hard links" |
| 7 | No path traversal (`..` or absolute paths in member names) | "Archive contains path traversal" |
| 8 | `flows.json` content is valid JSON and is a list | "flows.json is not valid JSON" / "flows.json must be a JSON array" |
| 9 | `flows.json` size <= 10 MB | "flows.json exceeds 10 MB" |

**Implementation note:** The uploaded file is read into memory via Django's `UploadedFile.read()`, then wrapped in `BytesIO` for `tarfile.open(fileobj=..., mode="r:gz")`. This avoids writing the untrusted file to disk before validation. The 50 MB size limit makes in-memory processing safe.

**Settings constant** in `config/settings.py`:
```python
IMPORT_MAX_SIZE = int(os.environ.get("IMPORT_MAX_SIZE", 50 * 1024 * 1024))
```

### 3. Storage

After validation succeeds, the archive is **re-created** (not copied) in the instance's `backup_dir`. This ensures:
- The archive follows the standard naming convention
- Only whitelisted files are included (defense in depth)
- The archive is stored in the correct instance directory

**Process:**
1. Generate filename: `flowhistory_YYYYMMDD_HHMMSS_<8-char-uuid>.tar.gz`
2. Ensure `config.backup_dir` exists
3. Create a new tar.gz archive at `config.backup_dir / filename` containing the validated files
4. Compute file size from the new archive on disk

### 4. Record Creation

A `BackupRecord` is created with:

| Field | Value |
|-------|-------|
| `config` | The target `NodeRedConfig` instance |
| `created_at` | `timezone.now()` (import time, not original backup time) |
| `filename` | Newly generated filename |
| `file_path` | Full path to the new archive in `config.backup_dir` |
| `file_size` | Size of the new archive on disk |
| `checksum` | SHA256 of the `flows.json` content from the uploaded archive |
| `status` | `"success"` |
| `trigger` | `"import"` (new trigger type) |
| `label` | User-provided label, or empty string |
| `notes` | User-provided notes, or empty string |
| `tab_summary` | Computed via `flow_parser.parse_flows()` |
| `changes_summary` | Computed via `diff_service.diff_tab_summaries()` against the last successful backup |
| `includes_credentials` | `True` if the uploaded archive contains `flows_cred.json` |
| `includes_settings` | `True` if the uploaded archive contains `settings.js` |

**Model change** -- add `"import"` to `TRIGGER_CHOICES` in `BackupRecord`:
```python
TRIGGER_CHOICES = [
    ("manual", "Manual"),
    ("scheduled", "Scheduled"),
    ("file_change", "File Change"),
    ("pre_restore", "Pre-Restore Safety"),
    ("import", "Import"),
]
```

**Dedup check:** Query for existing backups with the same checksum. If found, set `duplicate_warning` in the response but proceed with the import. The import is never blocked by a duplicate.

**Retention:** After creating the record, call `apply_retention(config)` exactly as `backup_service.create_backup()` does.

### 5. UI/UX

Add an "Import Backup" button to the instance dashboard header, next to the existing "Create Backup" button. The button uses `btn-secondary` to maintain visual hierarchy.

**Modal** -- built via JavaScript in `app.js`, following the `setNotes()` pattern:

```
+----------------------------------------------+
|  Import Backup                               |
|                                              |
|  [Choose file...]  No file selected          |
|                                              |
|  Label (optional)                            |
|  [_________________________________]         |
|                                              |
|  Notes (optional)                            |
|  [_________________________________]         |
|  [_________________________________]         |
|                                              |
|                     [Cancel]  [Import]        |
+----------------------------------------------+
```

- File input accepts `.tar.gz,.tgz`
- "Import" button submits via `fetch()` with `FormData`
- During upload, button text changes to "Importing..." and is disabled
- On success: show success banner, reload page
- On duplicate warning: show warning banner before reload
- On error: close modal, show error banner

### 6. Notifications

Add `IMPORT_SUCCESS` and `IMPORT_FAILED` events to the notification system.

**`backup/services/notifications/base.py`:**

| Event | Emoji | Color |
|-------|-------|-------|
| `import_success` | \U0001F4E5 (inbox tray) | Green (#10B981) |
| `import_failed` | \u274c (cross mark) | Red (#EF4444) |

### 7. Security

| Threat | Mitigation |
|--------|------------|
| Zip bomb / decompression bomb | 50 MB archive size limit; 10 MB per-file limit for flows.json |
| Path traversal (`../../etc/passwd`) | Reject members with `..` or absolute paths; archive re-created |
| Symlink attack | Reject any member where `issym()` or `islnk()` returns True |
| Unexpected file types | Whitelist: only `flows.json`, `flows_cred.json`, `settings.js` |
| Malicious JSON | Parsed via `json.loads()` in memory; must be a list; no eval |
| CSRF | Django's `CsrfViewMiddleware` active; `X-CSRFToken` header sent |
| Denial of service | `IMPORT_MAX_SIZE` setting; `DATA_UPLOAD_MAX_MEMORY_SIZE` set to match |

### 8. Testing

New test file: `backup/tests/test_import.py`

**ImportServiceTest** (unit tests for `import_service.py`):

| Test | Description |
|------|-------------|
| `test_valid_archive_import` | Valid `.tar.gz` with `flows.json` -- record created with `trigger="import"` |
| `test_archive_with_credentials_and_settings` | Archive with all three files -- flags set correctly |
| `test_tab_summary_populated` | Imported flows parsed, tab_summary set |
| `test_changes_summary_computed` | When previous backup exists, changes_summary has diffs |
| `test_checksum_computed` | SHA256 matches expected |
| `test_label_and_notes_saved` | Optional metadata stored |
| `test_duplicate_checksum_warns_but_imports` | Import succeeds with `duplicate_warning` |
| `test_reject_non_tar_gz` | Plain text file returns 400 |
| `test_reject_missing_flows_json` | Archive without flows.json returns 400 |
| `test_reject_unexpected_files` | Archive with extra files returns 400 |
| `test_reject_symlinks` | Archive with symlink returns 400 |
| `test_reject_path_traversal` | Path traversal returns 400 |
| `test_reject_invalid_flows_json` | Invalid JSON returns 400 |
| `test_reject_non_array_flows_json` | JSON object (not array) returns 400 |
| `test_reject_oversized_archive` | Exceeding `IMPORT_MAX_SIZE` returns 413 |
| `test_archive_stored_in_instance_backup_dir` | New archive in correct directory |
| `test_retention_applied_after_import` | `apply_retention()` called |
| `test_notification_sent_on_success` | `IMPORT_SUCCESS` dispatched |

**ApiImportBackupTest** (integration tests):

| Test | Description |
|------|-------------|
| `test_post_imports_backup` | Multipart POST returns 200 |
| `test_get_not_allowed` | GET returns 405 |
| `test_missing_file_returns_400` | POST without `archive` returns 400 |
| `test_invalid_archive_returns_400` | Non-tar.gz returns 400 |

### Files to Modify/Create

| File | Change |
|------|--------|
| `backup/services/import_service.py` | **New** -- validation + import logic |
| `backup/views/api.py` | Add `api_import_backup` view |
| `backup/views/__init__.py` | Add re-export |
| `backup/urls.py` | Add import URL pattern |
| `backup/models.py` | Add `"import"` to `TRIGGER_CHOICES` |
| `backup/migrations/0011_alter_backuprecord_trigger.py` | Migration for trigger choices |
| `backup/services/notifications/base.py` | Add import events, emoji, colors |
| `backup/templates/backup/instance_dashboard.html` | Add "Import Backup" button |
| `backup/static/backup/js/app.js` | Add `importBackup()` modal |
| `backup/tests/test_import.py` | **New** -- tests |
| `backup/tests/helpers.py` | Add `create_test_archive()` helper |
| `config/settings.py` | Add `IMPORT_MAX_SIZE` |
| `docs/adr/0000-adr-index.md` | Add ADR 0025 entry |

## Alternatives Considered

### Use JSON body with base64-encoded file
Rejected. Base64 inflates the file by ~33%. `multipart/form-data` via `FormData` is the standard web platform approach.

### Store the uploaded archive directly (no re-creation)
Rejected. Re-creating ensures only whitelisted files are included (defense in depth) and follows the standard naming convention.

### Add a separate ImportRecord model
Rejected. An imported backup is functionally identical to any other backup. The only distinction is the trigger type.

### Block import on duplicate checksum
Rejected. Users may intentionally import the same flows into different instances. A warning is informative; blocking is frustrating.

### Preserve original archive creation timestamp
Rejected. Using the original timestamp would break chronological ordering. Import time is when the backup entered *this* instance. Users can record the original date in notes.

### Put import logic in backup_service.py
Rejected. The import path has fundamentally different inputs (uploaded tar.gz) and validation requirements (untrusted data). A separate `import_service.py` keeps responsibilities clear.

## Consequences

**Positive:**
- Users can transfer backups between FlowHistory instances or import from local machines
- Imported backups are full first-class `BackupRecord` entries -- restore, diff, download, pin, label all work
- Security is defense-in-depth: size limits, whitelist validation, archive re-creation
- Follows existing patterns: API endpoint, service layer, JavaScript modal, tests
- Dedup warning is informative without blocking

**Negative:**
- First endpoint to use `multipart/form-data` instead of JSON body
- Re-created archive is not bit-identical to the uploaded file
- Adds a new trigger type to the model

## Todos

- [x] Add `"import"` to `BackupRecord.TRIGGER_CHOICES` and create migration
- [x] Add `IMPORT_MAX_SIZE` to `config/settings.py`
- [x] Create `backup/services/import_service.py`
- [x] Add `api_import_backup` view to `backup/views/api.py`
- [x] Add re-export to `backup/views/__init__.py`
- [x] Add URL pattern to `backup/urls.py`
- [x] Add `IMPORT_SUCCESS` / `IMPORT_FAILED` events to notification base
- [x] Add "Import Backup" button to instance dashboard template
- [x] Add `importBackup()` modal function to `app.js`
- [x] Add `create_test_archive()` helper to `backup/tests/test_import.py`
- [x] Create `backup/tests/test_import.py`
- [x] Run full test suite
