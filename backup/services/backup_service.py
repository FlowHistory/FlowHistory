"""Create and manage FlowHistory backup archives."""

import hashlib
import logging
import tarfile
import uuid
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from backup.models import BackupRecord, NodeRedConfig
from backup.services.diff_service import diff_tab_summaries, parse_flows_from_archive
from backup.services.flow_parser import get_tab_names, parse_flows_file

logger = logging.getLogger(__name__)


def create_backup(config=None, trigger="manual"):
    """Create a tar.gz backup of Node-RED files.

    Args:
        config: NodeRedConfig instance (fetched/created if None).
        trigger: One of "manual", "scheduled", "file_change", "pre_restore".

    Returns:
        BackupRecord on success, None if skipped (dedup match).
    """
    if config is None:
        config, _ = NodeRedConfig.objects.get_or_create(pk=1)

    flows_path = Path(config.flows_path)
    now = timezone.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    short_id = uuid.uuid4().hex[:8]
    filename = f"flowhistory_{timestamp}_{short_id}.tar.gz"
    dest = Path(settings.BACKUP_DIR) / filename

    # Validate flows.json exists
    if not flows_path.is_file():
        return _fail(config, filename, dest, trigger, f"flows.json not found at {flows_path}")

    # Compute checksum of flows.json for deduplication
    flows_bytes = flows_path.read_bytes()
    checksum = hashlib.sha256(flows_bytes).hexdigest()

    # Skip if identical to last successful backup (except manual/pre_restore)
    if trigger == "file_change" or (trigger == "scheduled" and not config.always_backup):
        last = (
            BackupRecord.objects
            .filter(config=config, status="success")
            .order_by("-created_at")
            .first()
        )
        if last and last.checksum == checksum:
            logger.info("Skipping backup — flows.json unchanged (checksum match)")
            return None

    # Build the tar.gz archive
    try:
        archive_size = _create_archive(dest, config, flows_path, flows_bytes)
    except OSError as e:
        return _fail(config, filename, dest, trigger, f"Failed to create archive: {e}")

    # Parse flow structure for tab summary
    tab_summary = get_tab_names(str(flows_path))

    # Compute changes vs previous backup
    changes_summary = _compute_changes(config, flows_path)

    # Save record
    record = BackupRecord.objects.create(
        config=config,
        created_at=now,
        filename=filename,
        file_path=str(dest),
        file_size=archive_size,
        checksum=checksum,
        status="success",
        trigger=trigger,
        tab_summary=tab_summary,
        changes_summary=changes_summary,
        includes_credentials=config.backup_credentials and _cred_path(config).is_file(),
        includes_settings=config.backup_settings and _settings_path(config).is_file(),
    )

    # Update config
    config.last_successful_backup = now
    config.last_backup_error = ""
    config.save(update_fields=["last_successful_backup", "last_backup_error"])

    logger.info("Backup created: %s (%d bytes)", filename, archive_size)

    # Run retention cleanup after successful backup
    try:
        from backup.services.retention_service import apply_retention

        apply_retention(config)
    except Exception:
        logger.warning("Retention cleanup failed after backup", exc_info=True)

    return record


def _create_archive(dest, config, flows_path, flows_bytes):
    """Create a tar.gz at dest containing the backup files. Returns archive size."""
    with tarfile.open(dest, "w:gz") as tar:
        # Always include flows.json
        _add_bytes_to_tar(tar, "flows.json", flows_bytes)

        # Optional: flows_cred.json
        cred_path = _cred_path(config)
        if config.backup_credentials and cred_path.is_file():
            tar.add(str(cred_path), arcname="flows_cred.json")

        # Optional: settings.js
        settings_path = _settings_path(config)
        if config.backup_settings and settings_path.is_file():
            tar.add(str(settings_path), arcname="settings.js")

    return dest.stat().st_size


def _add_bytes_to_tar(tar, arcname, data):
    """Add raw bytes to a tarfile with a proper TarInfo."""
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    info.mtime = timezone.now().timestamp()
    tar.addfile(info, BytesIO(data))


def _compute_changes(config, flows_path):
    """Compare current flows.json against the previous backup's flows.json.

    Returns a dict like:
        {"tabs_added": [...], "tabs_removed": [...], "tabs_modified": [...]}
    or empty dict if no previous backup exists.
    """
    last = (
        BackupRecord.objects
        .filter(config=config, status="success")
        .order_by("-created_at")
        .first()
    )
    if not last or not last.file_path:
        return {}

    last_archive = Path(last.file_path)
    if not last_archive.is_file():
        return {}

    # Extract previous flows.json from archive
    try:
        prev_parsed = parse_flows_from_archive(last_archive)
    except (tarfile.TarError, OSError, KeyError):
        return {}

    current_parsed = parse_flows_file(str(flows_path))
    if prev_parsed is None or current_parsed is None:
        return {}

    return diff_tab_summaries(prev_parsed, current_parsed)


def _cred_path(config):
    """Path to flows_cred.json alongside flows.json."""
    return Path(config.flows_path).parent / "flows_cred.json"


def _settings_path(config):
    """Path to settings.js alongside flows.json."""
    return Path(config.flows_path).parent / "settings.js"


def _fail(config, filename, dest, trigger, error_msg):
    """Record a failed backup attempt and return the failed BackupRecord."""
    logger.error("Backup failed: %s", error_msg)
    record = BackupRecord.objects.create(
        config=config,
        filename=filename,
        file_path=str(dest),
        status="failed",
        error_message=error_msg,
        trigger=trigger,
    )
    config.last_backup_error = error_msg
    config.save(update_fields=["last_backup_error"])
    return record
