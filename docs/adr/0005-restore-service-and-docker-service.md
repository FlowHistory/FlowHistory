# ADR 0005: Restore Service and Docker Service

## Status
Implemented

## Context

The application can create backups (ADR 0004) but cannot restore from them — completing the backup/restore cycle is the most critical missing feature. The architecture plan (ADR 0001 section 7) specifies a five-step restore strategy: verify, safety backup, extract, set ownership, and optionally restart Node-RED via the Docker socket.

The `NodeRedConfig` model already has `restart_on_restore` and `nodered_container_name` fields, and `docker-compose.yml` already mounts the Docker socket. The `docker` Python SDK is not yet in dependencies.

## Decision

### 1. Docker Service (`backup/services/docker_service.py`)

Wraps the Docker SDK to provide safe, optional container restart. Degrades gracefully when the SDK is missing or the socket is inaccessible.

**Functions:**
- `is_docker_available()` — returns True if the SDK is installed and the socket responds to ping
- `get_container_status(container_name)` — returns `{"name", "status"}` dict or None
- `restart_container(container_name, timeout=30)` — returns `{"success": bool, "message": str}`

**Import pattern:** The `docker` package is imported inside a try/except at module level. Every public function returns a safe failure result if the SDK is unavailable.

**Docker socket mount:** Changed from `:ro` to read-write, since restarting a container requires write access. Users who don't want restart capability can omit the mount entirely.

### 2. Restore Service (`backup/services/restore_service.py`)

`restore_backup(backup_id, restart=None)` orchestrates the full restore:

1. **Validate** the BackupRecord exists, has `status="success"`, and the archive file is present on disk
2. **Verify checksum** — extract flows.json from the archive, compute SHA256, compare against stored checksum
3. **Create pre-restore safety backup** via `create_backup(config, trigger="pre_restore")`. If it fails, log a warning but proceed (the user explicitly asked to restore)
4. **Extract to temp directory** first (under `BACKUP_DIR/_restore_tmp/`), then copy files to the Node-RED data directory. This avoids partial overwrites if extraction fails.
5. **Set ownership** to 1000:1000 and permissions to 0o644 on each restored file via `os.chown()` and `os.chmod()`
6. **Optionally restart** Node-RED container if `restart` is True or `config.restart_on_restore` is True
7. **Save RestoreRecord** with all metadata

Returns a `RestoreRecord` on both success and failure.

### 3. RestoreRecord Model

New model to track restore operations:

| Field | Type | Description |
|-------|------|-------------|
| `config` | ForeignKey | Parent config |
| `backup` | ForeignKey(null) | The backup that was restored |
| `safety_backup` | ForeignKey(null) | Pre-restore safety backup created |
| `created_at` | DateTimeField | When the restore happened |
| `status` | CharField(10) | success / failed |
| `error_message` | TextField | Error details |
| `container_restarted` | BooleanField | Whether Docker restart was attempted |
| `restart_message` | CharField(500) | Restart outcome message |
| `files_restored` | JSONField | List of file names that were written |

### 4. API Endpoint

`POST /api/restore/<int:backup_id>/` calls `restore_backup()` and returns:
- `200` with `{"status": "success", "restore": {...}}` on success
- `404` if backup not found
- `500` on failure with error message

### 5. Dashboard Integration

Each successful backup row gets a "Restore" button that confirms before POSTing to the restore API.

## Consequences

**Positive:**
- Completes the backup/restore cycle — the core use case now works end-to-end
- Pre-restore safety backup means restores are always reversible
- Extract-to-temp-then-copy prevents partial overwrites on failure
- Docker service degrades gracefully — restore works even without Docker access
- RestoreRecord provides full audit trail

**Negative:**
- Docker socket must be read-write for restart to work (security trade-off, opt-in)
- `os.chown()` requires the container to run as root
- No concurrent restore protection (acceptable for single-user app)
