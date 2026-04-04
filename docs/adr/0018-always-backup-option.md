# ADR 0018: Always Backup Option

## Status

Proposed

## Context

Scheduled backups use SHA256 checksum deduplication to skip creation when `flows.json` is unchanged since the last successful backup. While this saves disk space, it means a user checking the dashboard may see no recent scheduled backup and wonder whether the scheduler is working at all.

Some users prefer a guaranteed backup on every scheduled run regardless of changes, for peace of mind and auditability.

## Decision

Add an `always_backup` BooleanField (default `False`) to `NodeRedConfig`. When enabled, scheduled backups bypass the checksum deduplication check and always create a new archive.

### Scope

- **Scheduled backups**: respect `always_backup` setting
- **File-change backups**: always deduplicated (unchanged; prevents spam from rapid file events)
- **Manual / pre-restore backups**: already bypass dedup (unchanged)

### Model

```python
always_backup = models.BooleanField(default=False)
```

### Service logic change

```python
# Before
if trigger in ("scheduled", "file_change"):

# After
if trigger == "file_change" or (trigger == "scheduled" and not config.always_backup):
```

### Settings UI

Checkbox added to the Schedule section, below "Enable Scheduled Backups", with the label "Always Create Scheduled Backups" and help text "Create backup even when flows.json is unchanged".

## Consequences

**Positive:**
- Users get visible confirmation that scheduled backups ran
- No change in default behavior (opt-in only)
- Retention policy still applies, limiting disk growth from duplicate backups

**Negative:**
- Slightly more disk usage when enabled with unchanged flows
- Duplicate archives with identical content when flows haven't changed
