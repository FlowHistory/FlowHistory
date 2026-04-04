# ADR 0020: Simplify Default Settings and Reduce Environment Variables

## Status
Proposed

## Context

Several default settings don't match real-world usage patterns, and the environment variable surface area is larger than necessary for a Dockerized single-purpose tool:

1. **Debounce is too long (30s)**: If two deploys happen back-to-back within 30 seconds, only the second one gets captured because the debounce timer resets. The debounce exists to coalesce rapid filesystem events from a *single* deploy (Node-RED writes multiple times when saving), not to suppress separate deploys. A 3-second window is sufficient for that.

2. **Credentials included by default**: `backup_credentials` defaults to `True`, meaning `flows_cred.json` (containing encryption keys and secrets) is included in every backup archive. This is a poor security default — users who want credentials backed up should opt in explicitly.

3. **Redundant instance name + container name**: The settings page has both an "Instance Name" (display label, defaults to "Node-RED") and a "Container Name" (for Docker restart, defaults to "nodered"). The instance name serves no functional purpose — it's only used as a display label. Multi-instance support (ADR 0013) is still unimplemented, so there's no reason to maintain a separate display name. Remove the instance name field entirely.

4. **SECRET_KEY env var is unnecessary**: Django's `SECRET_KEY` already auto-generates via `secrets.token_urlsafe(50)` when not provided. For a single-user tool with optional auth, session invalidation on restart is acceptable (user just re-logs in). Having it in `.env.example` suggests it's required when it's not.

5. **NODERED_CONTAINER_NAME is redundant**: This value is configurable both as an env var AND on the settings page (`nodered_container_name` model field). The env var sets the Django setting, but the model field is what's actually used in `docker_service.py`. The env var is dead weight — remove it and let the settings page be the single source of truth.

6. **Docker socket not documented as required**: The `docker-compose.yml` mounts `/var/run/docker.sock` but this isn't documented. The socket is required for the container restart feature. Without it, `restart_on_restore` silently fails with a logged error.

## Decision

### 1. Reduce Debounce Default: 30s -> 3s

Change `watch_debounce_seconds` default from `30` to `3` in the model.

**Rationale**: 3 seconds is long enough to coalesce the multiple filesystem writes from a single Node-RED deploy, but short enough that two deploys 5 seconds apart each produce their own backup.

### 2. Disable Credential Backup by Default

Change `backup_credentials` default from `True` to `False` in the model.

**Rationale**: Secure by default. Users who need credentials backed up can enable it on the settings page. `backup_settings` already defaults to `False` — credentials should follow the same pattern.

### 3. Remove Instance Name Field

Remove the `name` field from `NodeRedConfig`. The app title is always "FlowHistory" and there's no multi-instance routing to disambiguate. If ADR 0013 (multi-instance) is ever implemented, a name field can be re-added at that time.

Update any templates or views that reference `config.name` to use a static string or remove the reference.

### 4. Remove SECRET_KEY from .env.example

Remove `SECRET_KEY=change-me-to-a-random-string` from `.env.example`. The auto-generation in `settings.py` is sufficient. Keep the auto-generation code as-is.

**Trade-off**: Sessions invalidate on container restart. Acceptable for a single-user tool — the user just re-authenticates.

### 5. Remove NODERED_CONTAINER_NAME Env Var

- Remove `NODERED_CONTAINER_NAME` from `.env.example` and `config/settings.py`
- The `nodered_container_name` model field (editable on settings page) is the single source of truth
- Update `docker_service.py` to read from the config model instead of `django.conf.settings` if it doesn't already

### 6. Document Docker Socket Requirement

Add a comment in `docker-compose.yml` and update the settings page help text for `restart_on_restore` to note that `/var/run/docker.sock` must be mounted for container restart to work.

## Summary of Environment Variable Changes

### Before (`.env.example`)
```
SECRET_KEY=change-me-to-a-random-string
DEBUG=false
ALLOWED_HOSTS=localhost,127.0.0.1,192.168.1.76
TIME_ZONE=America/New_York
NODERED_DATA_PATH=/nodered-data
NODERED_CONTAINER_NAME=nodered
REQUIRE_AUTH=false
APP_PASSWORD=
```

### After (`.env.example`)
```
DEBUG=false
ALLOWED_HOSTS=localhost,127.0.0.1,192.168.1.76
TIME_ZONE=America/New_York
NODERED_DATA_PATH=/nodered-data
REQUIRE_AUTH=false
APP_PASSWORD=
```

Removed: `SECRET_KEY`, `NODERED_CONTAINER_NAME`

## Summary of Model Default Changes

| Field | Old Default | New Default |
|-------|------------|-------------|
| `watch_debounce_seconds` | `30` | `3` |
| `backup_credentials` | `True` | `False` |
| `name` | `"Node-RED"` | *(field removed)* |

## Migration Notes

- Existing installations keep their current database values — default changes only affect fresh installs or new config rows
- The `name` field removal requires a migration that drops the column; existing data is lost (acceptable since it's cosmetic)
- No data migration needed for changed defaults — they only apply to `INSERT`, not `UPDATE`

## Consequences

### Positive
- Fewer env vars to configure (6 -> 4 in `.env.example`)
- Safer credential default
- Debounce matches real deploy cadence
- Single source of truth for container name (settings page, not env var)
- Less confusing setup for new users

### Negative
- Existing users with debounce at 30s will keep that value (no auto-migration), so behavior is unchanged for them but new installs differ
- Removing instance name is a one-way door — if multi-instance lands, the field gets re-added with a migration
- Sessions invalidate on restart when SECRET_KEY is not pinned (minor for single-user tool)
