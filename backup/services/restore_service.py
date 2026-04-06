"""Restore Node-RED files from a backup archive."""

import hashlib
import json
import logging
import os
import shutil
import tarfile
from pathlib import Path

from django.utils import timezone

from backup.models import BackupRecord, NodeRedConfig, RestoreRecord
from backup.services.backup_service import create_backup
from backup.services.docker_service import restart_container

logger = logging.getLogger(__name__)

# Ownership for restored files (Node-RED container user, configurable via env)
NODERED_UID = int(os.environ.get("NODERED_UID", "1000"))
NODERED_GID = int(os.environ.get("NODERED_GID", "1000"))
DEFAULT_FILE_MODE = 0o644
CREDENTIAL_FILE_MODE = 0o600  # flows_cred.json — owner only


def restore_backup(backup_id, restart=None):
    """Restore Node-RED files from a backup archive.

    Args:
        backup_id: PK of the BackupRecord to restore from.
        restart: Override for config.restart_on_restore. None uses config value.

    Returns:
        RestoreRecord (success or failed).

    Raises:
        BackupRecord.DoesNotExist: If backup_id is invalid.
    """
    record = BackupRecord.objects.select_related("config").get(pk=backup_id)
    config = record.config

    # Validate backup is restorable
    error = _validate_backup(record)
    if error:
        return _fail(config, record, None, error)

    # Verify checksum
    error = _verify_checksum(record)
    if error:
        return _fail(config, record, None, error)

    # Create pre-restore safety backup
    safety_backup = _create_safety_backup(config)

    if config.source_type == "remote":
        return _restore_remote(record, config, safety_backup)

    # Local restore: extract and copy files
    try:
        files_restored = _extract_and_restore(record, config)
    except Exception as e:
        return _fail(config, record, safety_backup, f"Failed to restore files: {e}")

    # Optionally restart Node-RED
    should_restart = restart if restart is not None else config.restart_on_restore
    container_restarted = False
    restart_message = ""

    if should_restart:
        result = restart_container(config.nodered_container_name)
        container_restarted = result["success"]
        restart_message = result["message"]

    # Save restore record
    restore_record = RestoreRecord.objects.create(
        config=config,
        backup=record,
        safety_backup=safety_backup,
        status="success",
        container_restarted=container_restarted,
        restart_message=restart_message,
        files_restored=files_restored,
    )

    logger.info("Restored from %s (%d files)", record.filename, len(files_restored))
    return restore_record


def _fail(config, backup, safety_backup, error_msg):
    """Record a failed restore attempt and return the RestoreRecord."""
    logger.error("Restore failed: %s", error_msg)
    return RestoreRecord.objects.create(
        config=config,
        backup=backup,
        safety_backup=safety_backup,
        status="failed",
        error_message=error_msg,
    )


def _validate_backup(record):
    """Return an error message if the backup can't be restored, or None."""
    if record.status != "success":
        return f"Cannot restore from a {record.status} backup"

    archive_path = Path(record.file_path)
    if not archive_path.is_file():
        return f"Archive not found: {record.file_path}"

    return None


def _verify_checksum(record):
    """Verify the flows.json inside the archive matches the stored checksum."""
    try:
        with tarfile.open(record.file_path, "r:gz") as tar:
            member = tar.getmember("flows.json")
            f = tar.extractfile(member)
            if f is None:
                return "Could not read flows.json from archive"
            content = f.read()
    except (tarfile.TarError, OSError, KeyError) as e:
        return f"Failed to read archive: {e}"

    actual = hashlib.sha256(content).hexdigest()
    if actual != record.checksum:
        return f"Checksum mismatch: expected {record.checksum[:12]}..., got {actual[:12]}..."

    return None


def _create_safety_backup(config):
    """Create a pre-restore safety backup. Returns BackupRecord or None."""
    try:
        flows_data = None
        if config.source_type == "remote":
            from backup.services.remote_service import fetch_remote_flows
            flows_data, _ = fetch_remote_flows(config)

        result = create_backup(config=config, trigger="pre_restore", flows_data=flows_data)
        if result and result.status == "success":
            logger.info("Pre-restore safety backup created: %s", result.filename)
            return result
        logger.warning("Safety backup did not succeed, proceeding with restore")
        return result
    except Exception:
        logger.warning("Failed to create safety backup, proceeding with restore", exc_info=True)
        return None


def _restore_remote(record, config, safety_backup):
    """Deploy flows from a backup archive to a remote Node-RED instance."""
    from backup.services.remote_service import deploy_remote_flows

    try:
        with tarfile.open(record.file_path, "r:gz") as tar:
            member = tar.getmember("flows.json")
            f = tar.extractfile(member)
            if f is None:
                return _fail(config, record, safety_backup, "Could not read flows.json from archive")
            flows_json = f.read()
    except (tarfile.TarError, OSError, KeyError) as e:
        return _fail(config, record, safety_backup, f"Failed to read archive: {e}")

    try:
        deploy_remote_flows(config, flows_json)
    except Exception as e:
        return _fail(config, record, safety_backup, f"Failed to deploy flows to remote instance: {e}")

    restore_record = RestoreRecord.objects.create(
        config=config,
        backup=record,
        safety_backup=safety_backup,
        status="success",
        files_restored=["flows.json"],
    )

    logger.info("Remote restore deployed to %s from %s", config.nodered_url, record.filename)
    return restore_record


def _extract_and_restore(record, config):
    """Extract archive to temp dir, then copy files to Node-RED data dir.

    Returns list of restored file names.
    """
    dest_dir = Path(config.flows_path).parent
    tmp_dir = config.backup_dir / "_restore_tmp"

    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # Extract archive to temp dir
        with tarfile.open(record.file_path, "r:gz") as tar:
            # Security: only extract known safe file names, skip symlinks
            safe_names = {"flows.json", "flows_cred.json", "settings.js"}
            members = [
                m for m in tar.getmembers()
                if m.name in safe_names and not m.issym() and not m.islnk()
            ]
            tar.extractall(path=tmp_dir, members=members)

        # Copy files to destination and set ownership
        files_restored = []
        for member in members:
            src = tmp_dir / member.name
            dst = dest_dir / member.name
            shutil.copy2(str(src), str(dst))
            try:
                os.chown(str(dst), NODERED_UID, NODERED_GID)
            except OSError:
                logger.warning("Could not chown %s (not running as root?)", dst)
            try:
                mode = CREDENTIAL_FILE_MODE if member.name == "flows_cred.json" else DEFAULT_FILE_MODE
                os.chmod(str(dst), mode)
            except OSError:
                logger.warning("Could not chmod %s", dst)
            files_restored.append(member.name)

        return files_restored
    finally:
        # Clean up temp dir
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
