# ADR 0009: Backup Label and Delete Endpoints

## Status
Implemented

## Context

The `BackupRecord` model already has a `label` field (CharField, max 200) but there is no API endpoint or UI to set or update it. ADR 0001 specifies `POST /api/backup/<id>/label/` for label management and `POST /backup/<id>/delete/` for backup deletion. The dashboard currently shows backup history but lacks the ability to annotate backups with user-supplied descriptions or remove unwanted backups.

Labels help users identify important backups (e.g., "before major refactor", "known good state") and are especially useful when retention policies would otherwise make old backups indistinguishable. Deletion lets users remove failed or irrelevant backups without waiting for retention cleanup.

## Decision

### 1. Label API Endpoint (`POST /api/backup/<id>/label/`)

Add `api_set_label` view in `backup/views.py`:
- Accept JSON body with `label` field (string, max 200 chars)
- Look up `BackupRecord` by id and config (singleton pk=1)
- Update `label` field and save
- Return JSON response with updated backup id and label
- Empty string clears the label
- Return 400 for missing/invalid input, 404 for unknown backup

Request body:
```json
{"label": "Before dashboard redesign"}
```

Response:
```json
{"status": "success", "backup": {"id": 42, "label": "Before dashboard redesign"}}
```

### 2. Delete Endpoint (`POST /backup/<id>/delete/`)

Add `backup_delete` view in `backup/views.py`:
- Look up `BackupRecord` by id and config
- Delete the archive file from disk if it exists
- Delete the database record
- Redirect to dashboard with a success message via Django messages framework
- Return 404 for unknown backup

This is a standard Django form POST (not a JSON API) since it redirects back to the dashboard. The dashboard and detail templates will include a delete button with a JavaScript confirmation dialog.

### 3. URL Registration

Add to `backup/urls.py`:
```python
path("api/backup/<int:backup_id>/label/", views.api_set_label, name="api_set_label"),
path("backup/<int:backup_id>/delete/", views.backup_delete, name="backup_delete"),
```

### 4. Dashboard UI Updates

**Label editing:**
- Add inline label display on backup detail page
- Add an edit button/icon that triggers a JavaScript prompt or inline input
- Call `POST /api/backup/<id>/label/` via fetch, update display on success

**Delete button:**
- Add delete button on backup detail page and optionally on dashboard rows
- JavaScript confirmation dialog ("Are you sure you want to delete this backup?")
- Submit as a form POST to `/backup/<id>/delete/`

### 5. JavaScript Updates (`backup/static/backup/js/app.js`)

Add two functions:
- `setLabel(backupId)` — prompt for label text, POST to API, update DOM
- `deleteBackup(backupId)` — confirm dialog, submit hidden form

### 6. Tests

Add test class `ApiSetLabelTest`:
- Test setting a label returns success
- Test clearing a label (empty string)
- Test 404 for nonexistent backup
- Test missing label field returns 400
- Test label exceeding 200 chars returns 400

Add test class `BackupDeleteTest`:
- Test successful deletion removes record and file
- Test deletion of record with missing file still succeeds
- Test 404 for nonexistent backup
- Test GET method not allowed (POST only)

## Consequences

**Positive:**
- Users can annotate backups for easy identification
- Users can remove unwanted backups without waiting for retention
- Completes two of the remaining items from ADR 0001's URL structure
- Label API is reusable (dashboard inline edit, detail page, future mobile/CLI clients)

**Negative:**
- Delete is irreversible — mitigated by confirmation dialog
- No undo for deletion — acceptable for a backup management tool where users understand permanence
