# ADR 0017: Bulk Actions

## Status
Proposed

## Context

All current backup operations (delete, pin/unpin, label, download) work on a single backup at a time via the per-row actions dropdown. When a user needs to clean up many old backups, pin a batch of known-good snapshots, or download several archives, they must repeat the same action once per row — clicking through a dropdown and confirmation dialog each time.

A bulk actions feature lets users select multiple backups and apply a single operation to the whole set.

## Decision

### 1. Row Selection Checkboxes

Add a checkbox column to the backup history table:

**Header checkbox** — a "select all" toggle in the `<thead>`:
```html
<th class="px-4 py-2 w-8">
  <input type="checkbox" id="select-all" onchange="toggleSelectAll(this)">
</th>
```

**Row checkboxes** — one per backup row, storing the backup ID:
```html
<td class="px-4 py-2">
  <input type="checkbox" class="backup-checkbox" value="{{ backup.pk }}" onchange="updateBulkBar()">
</td>
```

### 2. Bulk Action Bar

A sticky bar that appears at the bottom of the viewport when one or more checkboxes are selected. It shows the count and available actions:

```html
<div id="bulk-bar" class="hidden fixed bottom-0 inset-x-0 z-40 border-t ...">
  <span id="bulk-count">0 selected</span>
  <button onclick="bulkPin()">Pin</button>
  <button onclick="bulkUnpin()">Unpin</button>
  <button onclick="bulkDelete()">Delete</button>
  <button onclick="bulkDownload()">Download</button>
</div>
```

The bar hides automatically when no checkboxes are checked. Styling follows the existing card/dark-mode conventions.

### 3. Bulk API Endpoint (`POST /api/backup/bulk/`)

A single new endpoint that accepts a list of backup IDs and an action:

```python
path("api/backup/bulk/", views.api_bulk_action, name="api_bulk_action"),
```

Request body:
```json
{
  "ids": [1, 5, 12],
  "action": "delete"
}
```

Supported actions: `delete`, `pin`, `unpin`

Response:
```json
{
  "status": "success",
  "action": "delete",
  "affected": 3,
  "errors": []
}
```

Partial failures (e.g. one backup not found) do not abort the whole batch. The endpoint processes all valid IDs and returns errors for any that failed:
```json
{
  "status": "success",
  "action": "delete",
  "affected": 2,
  "errors": ["Backup 99 not found"]
}
```

### 4. View Implementation

Add `api_bulk_action` in `backup/views.py`:

```python
@require_POST
def api_bulk_action(request):
    data = json.loads(request.body)
    ids = data.get("ids", [])
    action = data.get("action")

    if action not in ("delete", "pin", "unpin"):
        return JsonResponse({"status": "error", "message": "Invalid action"}, status=400)

    if not ids or len(ids) > 100:
        return JsonResponse({"status": "error", "message": "Select 1-100 backups"}, status=400)

    backups = BackupRecord.objects.filter(pk__in=ids)
    errors = []
    affected = 0

    for backup in backups:
        try:
            if action == "delete":
                Path(backup.file_path).unlink(missing_ok=True)
                backup.delete()
            elif action == "pin":
                backup.is_pinned = True
                backup.save(update_fields=["is_pinned"])
            elif action == "unpin":
                backup.is_pinned = False
                backup.save(update_fields=["is_pinned"])
            affected += 1
        except Exception as e:
            errors.append(f"Backup {backup.pk}: {e}")

    missing = set(ids) - set(backups.values_list("pk", flat=True))
    for mid in missing:
        errors.append(f"Backup {mid} not found")

    return JsonResponse({"status": "success", "action": action, "affected": affected, "errors": errors})
```

### 5. Bulk Download

Download is handled client-side, not via the bulk API. When the user clicks "Download", the JS opens each selected backup's existing download URL (`/backup/<id>/download/`) in sequence. For small selections (up to ~10), this triggers individual browser downloads. No server-side ZIP bundling to keep complexity low.

### 6. JavaScript (`app.js`)

Add the following functions:

- `toggleSelectAll(checkbox)` — check/uncheck all `.backup-checkbox` inputs
- `updateBulkBar()` — count checked boxes, show/hide the bulk bar, update the count label
- `getSelectedIds()` — return array of checked backup IDs
- `bulkPin()`, `bulkUnpin()` — call `POST /api/backup/bulk/` with `action: "pin"` / `"unpin"`, reload on success
- `bulkDelete()` — confirm dialog ("Delete N backups? This cannot be undone."), then call bulk endpoint, reload on success
- `bulkDownload()` — iterate selected IDs and open `/backup/<id>/download/` for each

### 7. Confirmation Behavior

- **Delete** always shows a confirm dialog with the count
- **Pin/Unpin** apply immediately (no confirmation — non-destructive, easily reversible)
- **Download** starts immediately (user-initiated, no side effects)

## Consequences

**Positive:**
- Eliminates repetitive per-row clicking for batch operations
- Single new API endpoint keeps the surface area small
- Partial-failure handling prevents one bad ID from blocking the rest
- Checkbox + sticky bar is a well-understood UI pattern
- No new dependencies or complex state management

**Negative:**
- Adds a checkbox column that slightly reduces horizontal space on the table
- Bulk delete bypasses the existing form-based delete flow (uses API instead), but this is acceptable since the confirmation dialog still protects against accidents
- No server-side ZIP for bulk download — users downloading many files will get individual downloads (acceptable for the typical use case of a few backups)
