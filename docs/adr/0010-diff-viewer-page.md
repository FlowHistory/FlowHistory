# ADR 0010: Diff Viewer Page

## Status
Implemented

## Context

The diff service (`backup/services/diff_service.py`) and flow parser are fully implemented, and the detail page already renders stored `changes_summary` inline with color-coded node badges. However, there is no dedicated diff viewer page as specified in ADR 0001 (`/diff/<id>/` for diff vs previous, `/diff/<id_a>/<id_b>/` for comparing two arbitrary backups). A full-page diff view enables richer presentation, arbitrary two-backup comparison via a dropdown selector, and a tab overview panel.

The original diff service only compared nodes within tabs, missing changes inside subflows. It also only reported which fields changed (by name) without showing the actual values.

## Decision

### 1. Extract Node Badge Component

Extract the repeated node badge pill markup from `detail.html` into a reusable template partial `backup/templates/backup/components/_node_badge.html`. Accepts `node` (dict with `type`, optional `name`, `group`, `changed_fields`) and `variant` (`success`/`danger`/`warning`).

### 2. Single View, Two URL Routes

Add a single `diff_view(request, backup_id, compare_id=None)` function serving two URL patterns:
```python
path("diff/<int:backup_id>/", views.diff_view, name="diff_vs_previous"),
path("diff/<int:backup_id>/<int:compare_id>/", views.diff_view, name="diff_compare"),
```

### 3. Diff Data Strategy

- **`/diff/<id>/` (vs previous):** Try archive diff first for full detail. Fall back to stored `BackupRecord.changes_summary` when archives are unavailable (deleted by retention).
- **`/diff/<id_a>/<id_b>/` (arbitrary comparison):** Always call `diff_backup_archives()` since no pre-stored diff exists for arbitrary pairs.
- Auto-sort by `created_at` so backup_a is always the older one.
- Catch `FileNotFoundError`/`tarfile.TarError` and render a clear error message instead of 500.

### 4. Subflow Diffing

Extend `diff_tab_summaries` to compare subflows alongside tabs. The return structure now includes `subflows_added`, `subflows_removed`, and `subflows_modified` keys, using the same node-level diff format as tabs. Internally refactored to a shared `_diff_container_set` function that handles both tabs (keyed by `label`) and subflows (keyed by `name`).

### 5. Field-Level Unified Diffs

For modified nodes, the diff service now produces `field_diffs`: a list of per-field diffs using Python's `difflib.unified_diff`. Multi-line string fields (like `func`, `info`) get git-style unified diffs with `+`/`-` line markers. Simple values show `- old` / `+ new`. The view classifies each line as `add`/`remove`/`header`/`context` for color-coded rendering in `<pre>` blocks.

### 6. Diff Template (`diff.html`)

Full-page template extending `base.html` with:
- **Header:** Two-column display showing Base and Current backup metadata (dates, labels, trigger badges)
- **Comparison selector:** `<select>` dropdown of available backups with a "Compare" button that navigates via JS to `/diff/<id>/<compareId>/`
- **Summary stats:** 5 stat cards (tabs changed/added/removed, subflows changed, nodes changed)
- **Tab overview panel:** All tabs from both versions as color-coded badges (green=added, red=removed, yellow=modified, gray=unchanged)
- **Tab diff sections:** Added list, Removed list, Modified as collapsible `<details>` with node badges and field-level diffs
- **Subflow diff sections:** Same structure as tabs
- **Empty states:** Messages for "no changes" and "first backup"

Reusable `_diff_nodes.html` component renders the node-level diff for both tabs and subflows, including expandable field-level diffs with syntax-highlighted unified diff output.

### 7. Detail Page Integration

Add a "View Diff" button to the backup detail page action row, linking to `diff_vs_previous`.

### 8. Tests

Add test classes covering:
- `DiffViewTest` (10 tests): view responses, stored summary fallback, archive error handling, comparison dropdown
- `DiffTabSummariesTest` additions (5 tests): subflow add/remove/modify, field-level unified diff format, simple value diffs

## Consequences

**Positive:**
- Dedicated diff page enables richer presentation than inline summary on detail page
- Arbitrary two-backup comparison unlocks cross-version analysis
- Subflow changes are now visible (previously invisible)
- Field-level unified diffs show exactly what changed, like `git diff`
- Collapsible sections handle large diffs without overwhelming the page
- Extracted `_node_badge.html` and `_diff_nodes.html` components eliminate duplicated markup

**Negative:**
- Arbitrary comparison requires opening and parsing two archive files on each request (mitigated by small file sizes ~150KB compressed)
- Archives deleted by retention fall back to stored summary (which lacks field-level detail)
- `field_diffs` are not stored in `changes_summary` — only available when archives exist
