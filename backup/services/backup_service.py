"""Create and manage FlowHistory backup archives."""

import hashlib
import json
import logging
import tarfile
import uuid
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from backup.models import BackupRecord
from backup.services.diff_service import diff_tab_summaries, parse_flows_from_archive
from backup.services.flow_parser import parse_flows

logger = logging.getLogger(__name__)


def create_backup(config, trigger="manual", flows_data=None):
    """Create a tar.gz backup of Node-RED files.

    Args:
        config: NodeRedConfig instance.
        trigger: One of "manual", "scheduled", "file_change", "pre_restore".
        flows_data: Raw flows JSON (str or bytes). If provided, used instead of
                    reading from config.flows_path (for remote instances).

    Returns:
        BackupRecord on success, None if skipped (dedup match).
    """
    now = timezone.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    short_id = uuid.uuid4().hex[:8]
    filename = f"flowhistory_{timestamp}_{short_id}.tar.gz"
    backup_dir = config.backup_dir
    if not str(backup_dir.resolve()).startswith(str(Path(settings.BACKUP_DIR).resolve())):
        return _fail(config, filename, Path(""), trigger, "Backup directory outside allowed path")
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / filename

    if flows_data is not None:
        flows_bytes = flows_data if isinstance(flows_data, bytes) else flows_data.encode()
        is_local = False
    else:
        flows_path = Path(config.flows_path)
        if not flows_path.is_file():
            return _fail(config, filename, dest, trigger, f"flows.json not found at {flows_path}")
        flows_bytes = flows_path.read_bytes()
        is_local = True

    checksum = hashlib.sha256(flows_bytes).hexdigest()

    # Fetch last backup once — used for both dedup and changes
    last = (
        BackupRecord.objects
        .filter(config=config, status="success")
        .order_by("-created_at")
        .first()
    )

    if trigger == "file_change" or (trigger == "scheduled" and not config.always_backup):
        if last and last.checksum == checksum:
            logger.info("Skipping backup — flows.json unchanged (checksum match)")
            return None

    try:
        archive_size = _create_archive(dest, config, is_local, flows_bytes)
    except OSError as e:
        return _fail(config, filename, dest, trigger, f"Failed to create archive: {e}")

    current_parsed = _parse_flows_bytes(flows_bytes)
    tab_summary = [t["label"] for t in current_parsed["tabs"]] if current_parsed else []
    changes_summary = _compute_changes(last, current_parsed)

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
        includes_credentials=is_local and config.backup_credentials and _cred_path(config).is_file(),
        includes_settings=is_local and config.backup_settings and _settings_path(config).is_file(),
    )

    config.last_successful_backup = now
    config.last_backup_error = ""
    config.save(update_fields=["last_successful_backup", "last_backup_error"])

    logger.info("Backup created: %s (%d bytes)", filename, archive_size)

    _notify_backup(config, record)

    try:
        from backup.services.retention_service import apply_retention
        apply_retention(config)
    except Exception:
        logger.warning("Retention cleanup failed after backup", exc_info=True)

    return record


def _parse_flows_bytes(flows_bytes):
    """Parse flows JSON bytes into structured summary. Returns None on failure."""
    try:
        return parse_flows(json.loads(flows_bytes))
    except (json.JSONDecodeError, TypeError):
        return None


def _create_archive(dest, config, is_local, flows_bytes):
    """Create a tar.gz at dest containing the backup files. Returns archive size."""
    with tarfile.open(dest, "w:gz") as tar:
        _add_bytes_to_tar(tar, "flows.json", flows_bytes)

        if is_local:
            cred_path = _cred_path(config)
            if config.backup_credentials and cred_path.is_file():
                tar.add(str(cred_path), arcname="flows_cred.json")

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


def _compute_changes(last_backup, current_parsed):
    """Compare current parsed flows against the previous backup's flows.

    Args:
        last_backup: Last successful BackupRecord, or None.
        current_parsed: Parsed flow structure from parse_flows(), or None.

    Returns:
        Dict with tabs_added/removed/modified, or empty dict.
    """
    if not last_backup or not last_backup.file_path or current_parsed is None:
        return {}

    last_archive = Path(last_backup.file_path)
    if not last_archive.is_file():
        return {}

    try:
        prev_parsed = parse_flows_from_archive(last_archive)
    except (tarfile.TarError, OSError, KeyError):
        return {}

    if prev_parsed is None:
        return {}

    return diff_tab_summaries(prev_parsed, current_parsed)


def _cred_path(config):
    return Path(config.flows_path).parent / "flows_cred.json"


def _settings_path(config):
    return Path(config.flows_path).parent / "settings.js"


def _notify_backup(config, record):
    """Send notification for a backup result."""
    try:
        from backup.services.notification_service import notify
        from backup.services.notifications.base import NotificationPayload, NotifyEvent

        is_success = record.status == "success"
        payload = NotificationPayload(
            event=NotifyEvent.BACKUP_SUCCESS if is_success else NotifyEvent.BACKUP_FAILED,
            instance_name=config.name,
            instance_slug=config.slug,
            instance_color=config.color,
            title=f"Backup {'successful' if is_success else 'failed'} \u2014 {config.name}",
            message=record.filename if is_success else "Backup attempt failed.",
            error=record.error_message if not is_success else None,
            filename=record.filename,
            file_size=record.file_size if is_success else None,
            trigger=record.trigger,
        )
        notify(config, payload)
    except Exception:
        logger.warning("Notification failed after backup", exc_info=True)


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
    _notify_backup(config, record)
    return record
