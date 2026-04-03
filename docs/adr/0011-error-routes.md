# ADR 0011: Error Routes (404/500) with Redirect and Flash Messages

## Status
Implemented

## Context

When users navigate to a non-existent page or reference a backup ID that doesn't exist, Django shows its default error pages — bare, unstyled HTML with no navigation or branding. In production (`DEBUG=False`), these are especially unhelpful: a plain "Not Found" or "Server Error" with no way back to the application.

Since this is a single-purpose tool (not a multi-page SPA), there's no strong reason to show a dedicated error page. Redirecting to the dashboard with a flash message is simpler and more user-friendly — the user lands on a functional page and sees what went wrong.

## Decision

### 1. Page Views: Replace `get_object_or_404` with Redirect Pattern

For page views that render HTML (`backup_detail`, `backup_download`, `diff_view`, `backup_delete`), replace `get_object_or_404()` with a try/except that uses Django's messages framework and redirects to the dashboard:

```python
try:
    backup = BackupRecord.objects.get(pk=backup_id, config=config)
except BackupRecord.DoesNotExist:
    messages.error(request, "Backup not found.")
    return redirect("dashboard")
```

API views (`api_set_label`, `api_restore_backup`) continue returning JSON 404 responses — they serve programmatic clients, not browsers.

### 2. Global 404 Handler

Set `handler404` in `config/urls.py` pointing to a custom view that redirects to the dashboard with a "Page not found" flash message. This catches any URL that doesn't match a defined route.

### 3. Global 500 Handler

Set `handler500` in `config/urls.py` pointing to a custom view that redirects to the dashboard with a generic error message. This replaces Django's default 500 page.

### Files Modified

| File | Change |
|------|--------|
| `backup/views.py` | Replace `get_object_or_404` in page views; add `custom_404` and `custom_500` handlers |
| `config/urls.py` | Add `handler404` and `handler500` assignments |

## Consequences

- Users always land on a functional page with clear feedback instead of a dead-end error page
- API consumers are unaffected — JSON error responses remain unchanged
- No custom error templates needed (reduces maintenance surface)
- Trade-off: users don't see the attempted URL in the error message, but for this app's scope that's acceptable
