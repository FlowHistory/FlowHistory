# ADR 0007: Settings Page

## Status
Proposed

## Context

All `NodeRedConfig` fields are currently only editable via the Django admin or direct database manipulation. Users need a web UI to configure backup frequency, retention limits, file watching, restore behavior, and other settings without restarting the container or accessing the database.

The architecture overview (ADR 0001) specifies a `/settings/` route with a configuration form, and the `NodeRedConfig` model already exists with all the necessary fields.

## Decision

### 1. Settings View (`backup/views.py`)

Add a `settings_view` function handling GET and POST:
- **GET**: Render a form pre-populated with the current `NodeRedConfig` (singleton, pk=1)
- **POST**: Validate and save the form, redirect back to settings with a success message

Use a Django `ModelForm` for `NodeRedConfig` to get built-in validation. Exclude read-only status fields (`last_successful_backup`, `last_backup_error`) from the form.

### 2. Settings Form (`backup/forms.py`)

`NodeRedConfigForm(ModelForm)` with:
- All user-configurable fields from `NodeRedConfig`
- Logical field grouping via fieldsets rendered in the template:
  - **General**: `name`, `flows_path`
  - **Schedule**: `is_active`, `backup_frequency`, `backup_time`, `backup_day`
  - **File Watching**: `watch_enabled`, `watch_debounce_seconds`
  - **Backup Contents**: `backup_credentials`, `backup_settings`
  - **Retention**: `max_backups`, `max_age_days`
  - **Restore**: `restart_on_restore`, `nodered_container_name`
- Clean method validation: `backup_day` only required when `backup_frequency` is weekly

### 3. Settings Template (`backup/templates/backup/settings.html`)

- Extends `base.html`, consistent with dashboard styling
- Bootstrap 5 card-based layout with one card per fieldset group
- Form fields rendered with Bootstrap form classes
- Success/error feedback via Django messages framework
- Cancel button returns to dashboard

### 4. URL Registration

Add `path("settings/", views.settings_view, name="settings")` to `backup/urls.py`.

### 5. Navigation

Add a Settings link to the base template navigation bar, linking to the settings page.

### 6. Dynamic Config Behavior

Most settings take effect immediately since services re-read `NodeRedConfig` from the database:
- Watcher service re-reads config on each file event (debounce time, watch_enabled)
- Retention service reads config on each run (max_backups, max_age_days)
- Backup service reads config on each backup (backup_credentials, backup_settings)

**Exception**: Scheduler frequency/time changes require a container restart because APScheduler's `CronTrigger` is set at startup. The settings page should display a note about this.

## Consequences

**Positive:**
- Users can configure all backup settings through the web UI
- No database access or container restart needed for most changes
- Form validation prevents invalid configurations
- Grouped layout makes settings discoverable

**Negative:**
- Schedule changes still require container restart (documented in UI)
- Single-instance model pattern means no multi-instance support (acceptable per ADR 0001)
