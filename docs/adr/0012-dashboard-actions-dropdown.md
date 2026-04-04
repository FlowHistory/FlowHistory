# ADR 0012: Dashboard Actions Dropdown

## Status
Proposed

## Context

All backup actions (download, delete, label, diff) are implemented but only accessible from the backup detail page. The dashboard table's Actions column currently shows only a Restore button for successful backups and a dash for failed ones. Users must click through to the detail page to perform common operations like downloading or deleting a backup.

Adding an actions dropdown to each table row lets users perform these operations without leaving the dashboard — fewer clicks for the most common workflows.

## Decision

### 1. Replace Single Restore Button with Actions Dropdown

Replace the current Restore button in the Actions column with a dropdown menu containing all available actions for each backup row.

**For successful backups:**
- Label (calls existing `setLabel()`)
- Download (links to existing download URL)
- Diff (links to existing diff vs previous URL)
- Restore (calls existing `restoreBackup()`)
- Delete (calls existing `deleteBackup()`)

**For failed backups:**
- Label
- Delete

### 2. Dropdown Inline in Dashboard

The dropdown is inlined directly in the dashboard template (not a separate component) since the menu items vary per row based on backup status. Triggered by a "..." button (ellipsis), opens on click, closes when clicking outside.

### 3. JavaScript

Add a lightweight `toggleDropdown()` function and a global click-outside listener to `app.js`. No external library needed.

### 4. Separator Before Destructive Actions

Add a visual divider in the dropdown before Restore and Delete to separate non-destructive actions from destructive ones.

### Files Modified

| File | Change |
|------|--------|
| `backup/templates/backup/dashboard.html` | Replace Restore button with actions dropdown |
| `backup/static/backup/js/app.js` | Add `toggleDropdown()` and click-outside handler |
| `backup/static/backup/css/input.css` | Add dropdown menu styles via `@apply` |

## Consequences

- Users can perform all backup operations directly from the dashboard
- Consistent with modern web UI patterns (three-dot action menus)
- Dropdown component is reusable for future tables
- No additional backend changes required — all endpoints already exist
- Trade-off: slightly more complex JS, but minimal (~15 lines)
