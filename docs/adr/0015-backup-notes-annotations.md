# ADR 0015: Backup Notes/Annotations

## Status
Implemented

## Context

The `BackupRecord` model has a `label` field (CharField, max 200 chars) used as a short one-line tag for quick identification (e.g., "before major refactor"). This was implemented in ADR 0009 with a simple JSON API (`POST /api/backup/<id>/label/`) and inline prompt-based UI on the dashboard.

However, users often want to record *why* a particular flow state matters — not just a short tag but a richer description. Examples:

- "Rewired the MQTT → InfluxDB pipeline to use batch writes. Old approach was one-write-per-message and hammering the DB. Revert here if latency spikes."
- "Added new subflow for Zigbee device auto-discovery. Still experimental — may roll back after a week of testing."
- "Known-good baseline before upgrading node-red-contrib-home-assistant to v2.x"

A 200-character label cannot capture this level of context. The current label field should remain as a short tag, with a separate `notes` field providing space for longer-form annotations.

## Decision

### 1. Model Change

Add a `notes` field to `BackupRecord` in `backup/models.py`:

```python
notes = models.TextField(blank=True, default="")
```

This is a free-form text field with no length limit enforced at the model level. The UI will provide a `<textarea>` for editing.

### 2. Notes API Endpoint (`POST /api/backup/<id>/notes/`)

Add `api_set_notes` view in `backup/views.py`:

- Accept JSON body with `notes` field (string)
- Look up `BackupRecord` by id and config (singleton pk=1)
- Update `notes` field and save
- Return JSON response with updated backup id and notes
- Empty string clears the notes
- Return 400 for missing/invalid input, 404 for unknown backup

Request body:
```json
{"notes": "Rewired MQTT pipeline to batch writes. Revert here if latency spikes."}
```

Response:
```json
{"status": "success", "backup": {"id": 42, "notes": "Rewired MQTT pipeline..."}}
```

### 3. URL Registration

Add to `backup/urls.py`:
```python
path("api/backup/<int:backup_id>/notes/", views.api_set_notes, name="api_set_notes"),
```

### 4. Dashboard UI

Add a "Notes" action to the existing actions dropdown (alongside "Label" and "Delete"):

- Clicking "Notes" opens a modal dialog with a `<textarea>` pre-filled with the current notes
- Save button sends `POST /api/backup/<id>/notes/` via fetch and updates the row
- The dashboard table does **not** show notes inline (too long) — instead, a small icon/indicator appears next to backups that have notes

### 5. Detail Page

The backup detail page (`/backup/<id>/`) will display the full notes content below the label, rendered as plain text with preserved line breaks (using `|linebreaksbr` filter). An edit button opens the same modal or an inline `<textarea>` for editing.

### 6. Diff Page

The diff page (`/diff/<id>/`) will show the notes for both the "before" and "after" backups in the header area, providing context about what changed and why.

## Consequences

**Positive:**
- Users can record meaningful context about why a flow state matters, making it easier to decide which backup to restore
- The short label remains for quick scanning; notes provide depth when needed
- Follows the same API pattern as the existing label endpoint (ADR 0009), keeping the codebase consistent
- Notes indicator on dashboard avoids cluttering the table while surfacing that context exists

**Negative:**
- Adds another field to maintain and display across dashboard, detail, and diff pages
- TextField with no hard limit could theoretically store very large text (mitigated by textarea UI which naturally constrains input length)
- Modal-based editing adds JavaScript complexity (mitigated by reusing the same pattern as the label prompt, upgraded to a modal)
