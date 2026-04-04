# ADR 0016: Snapshot Pinning

## Status
Implemented

## Context

The retention service (ADR 0006) automatically deletes backups based on two policies: age (`max_age_days`) and count (`max_backups`). The only current protection is a 24-hour grace period for `pre_restore` safety backups.

Users sometimes want to preserve specific backups indefinitely — known-good baselines, pre-upgrade snapshots, or milestones they may need to restore weeks or months later. Today, the only way to prevent retention from deleting an important backup is to increase the global retention limits, which keeps *all* old backups rather than just the ones that matter.

A "pin" mechanism lets users explicitly protect individual backups from retention cleanup while leaving global retention policies tight.

## Decision

### 1. Model Change

Add an `is_pinned` boolean field to `BackupRecord` in `backup/models.py`:

```python
is_pinned = models.BooleanField(default=False)
```

All existing backups default to unpinned. Pinning is purely a retention shield — it does not prevent manual deletion.

### 2. Retention Service Changes

Update `apply_retention()` in `backup/services/retention_service.py` to skip pinned backups in both deletion stages:

**Age-based deletion** — add `.exclude(is_pinned=True)` to the queryset:
```python
old_backups = (
    BackupRecord.objects
    .filter(config=config, status="success", created_at__lt=age_cutoff)
    .exclude(trigger="pre_restore", created_at__gte=protected_cutoff)
    .exclude(is_pinned=True)
)
```

**Count-based deletion** — filter out pinned backups from the excess list:
```python
excess = [
    r for r in excess
    if not (r.trigger == "pre_restore" and r.created_at >= protected_cutoff)
    and not r.is_pinned
]
```

Pinned backups still count toward the `max_backups` total (they occupy a slot). This prevents a scenario where pinning many backups causes aggressive deletion of unpinned ones.

### 3. Pin Toggle API Endpoint (`POST /api/backup/<id>/pin/`)

Add `api_toggle_pin` view in `backup/views.py`:

- Toggle `is_pinned` field on the `BackupRecord`
- Return JSON response with updated backup id and pin state
- Return 404 for unknown backup

Request body: *(none — toggle on each call)*

Response:
```json
{"status": "success", "backup": {"id": 42, "is_pinned": true}}
```

### 4. URL Registration

Add to `backup/urls.py`:
```python
path("api/backup/<int:backup_id>/pin/", views.api_toggle_pin, name="api_toggle_pin"),
```

### 5. Dashboard UI

Add a "Pin" / "Unpin" action to the existing actions dropdown (alongside Label, Notes, Delete):

- Label shows "Pin" for unpinned backups, "Unpin" for pinned ones
- Clicking sends `POST /api/backup/<id>/pin/` via fetch and updates the row
- Pinned backups display a pin icon (📌 or SVG equivalent) in the table row as a visual indicator
- The pin icon appears near the label/notes indicators for consistency

### 6. Detail Page

The backup detail page (`/backup/<id>/`) shows the pinned status with a badge or icon, and provides a pin/unpin button.

### 7. Manual Deletion Behavior

Pinning does **not** prevent manual deletion via the Delete action. If a user explicitly deletes a pinned backup, it is removed as normal. The pin only protects against automated retention cleanup.

## Consequences

**Positive:**
- Users can protect important backups without inflating global retention limits
- Simple boolean toggle is easy to understand — no complex retention tiers or policies
- Follows the same API pattern as label (ADR 0009) and notes (ADR 0015), keeping the codebase consistent
- Pinned backups counting toward `max_backups` prevents retention starvation

**Negative:**
- If many backups are pinned, the effective retention window for unpinned backups shrinks (since pinned ones occupy count slots)
- Adds another field to display across dashboard and detail pages (mitigated by a small icon indicator)
