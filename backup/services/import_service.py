"""Import backup archives uploaded by the user."""

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

_ALLOWED_MEMBERS = frozenset({"flows.json", "flows_cred.json", "settings.js"})
_MAX_FLOWS_SIZE = 10 * 1024 * 1024  # 10 MB


class ImportValidationError(Exception):
    """Raised when an uploaded archive fails validation."""


def validate_import_archive(uploaded_file):
    """Validate an uploaded backup archive and return its extracted contents.

    Args:
        uploaded_file: Django UploadedFile instance.

    Returns:
        dict mapping member names to their bytes content.

    Raises:
        ImportValidationError on any validation failure.
    """
    # 1. Size check
    if uploaded_file.size > settings.IMPORT_MAX_SIZE:
        max_mb = settings.IMPORT_MAX_SIZE // (1024 * 1024)
        raise ImportValidationError(f"Archive exceeds maximum size of {max_mb} MB")

    # 2. Filename extension
    name = uploaded_file.name or ""
    if not (name.endswith(".tar.gz") or name.endswith(".tgz")):
        raise ImportValidationError("File must be a .tar.gz archive")

    # 3. Read into memory and open as tar.gz
    raw = uploaded_file.read()
    try:
        with tarfile.open(fileobj=BytesIO(raw), mode="r:gz") as tar:
            members = tar.getmembers()
            # 4. Reject archives with too many or duplicate members
            if len(members) > len(_ALLOWED_MEMBERS):
                raise ImportValidationError("Archive contains too many files")

            member_names = set()
            for m in members:
                if m.name in member_names:
                    raise ImportValidationError(
                        f"Archive contains duplicate file: {m.name}"
                    )
                member_names.add(m.name)

            # 5. Must contain flows.json
            if "flows.json" not in member_names:
                raise ImportValidationError("Archive must contain flows.json")

            # 6. No unexpected files
            unexpected = member_names - _ALLOWED_MEMBERS
            if unexpected:
                raise ImportValidationError(
                    "Archive contains unexpected files: "
                    f"{', '.join(sorted(unexpected))}"
                )

            # 7. No symlinks or hardlinks
            for m in members:
                if m.issym() or m.islnk():
                    raise ImportValidationError(
                        "Archive contains symbolic or hard links"
                    )

            # 8. No path traversal
            for m in members:
                if ".." in m.name or m.name.startswith("/"):
                    raise ImportValidationError("Archive contains path traversal")

            # Extract all members into a dict (check size before reading)
            contents = {}
            for m in members:
                if m.size > _MAX_FLOWS_SIZE:
                    raise ImportValidationError(
                        f"{m.name} exceeds maximum uncompressed size of "
                        f"{_MAX_FLOWS_SIZE // (1024 * 1024)} MB"
                    )
                f = tar.extractfile(m)
                if f is None:
                    raise ImportValidationError(f"Cannot read {m.name} from archive")
                contents[m.name] = f.read()
    except ImportValidationError:
        raise
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise ImportValidationError("File is not a valid tar.gz archive") from exc

    # 9. flows.json must be valid JSON array
    flows_bytes = contents["flows.json"]

    try:
        flows_data = json.loads(flows_bytes)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ImportValidationError("flows.json is not valid JSON") from exc

    if not isinstance(flows_data, list):
        raise ImportValidationError("flows.json must be a JSON array")

    return contents


def import_backup(config, uploaded_file, label="", notes=""):
    """Import an uploaded backup archive for the given instance.

    Args:
        config: NodeRedConfig instance.
        uploaded_file: Django UploadedFile instance.
        label: Optional label for the backup.
        notes: Optional notes for the backup.

    Returns:
        tuple of (BackupRecord, duplicate_warning_or_None).

    Raises:
        ImportValidationError on validation failure.
    """
    if label and len(label) > 200:
        raise ImportValidationError("Label must be 200 characters or fewer")

    contents = validate_import_archive(uploaded_file)

    flows_bytes = contents["flows.json"]
    checksum = hashlib.sha256(flows_bytes).hexdigest()

    # Check for duplicate
    existing = (
        BackupRecord.objects.filter(config=config, status="success", checksum=checksum)
        .order_by("-created_at")
        .first()
    )
    duplicate_warning = None
    if existing:
        duplicate_warning = (
            f"Checksum matches existing backup '{existing.filename}'"
            f" from {existing.created_at.strftime('%Y-%m-%d %H:%M')}"
        )

    # Generate filename and store archive
    now = timezone.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    short_id = uuid.uuid4().hex[:8]
    filename = f"flowhistory_{timestamp}_{short_id}.tar.gz"

    backup_dir = config.backup_dir
    if not backup_dir.resolve().is_relative_to(Path(settings.BACKUP_DIR).resolve()):
        raise ImportValidationError("Backup directory outside allowed path")

    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / filename

    # Re-create archive with only validated files
    with tarfile.open(dest, "w:gz") as tar:
        for member_name, data in contents.items():
            info = tarfile.TarInfo(name=member_name)
            info.size = len(data)
            info.mtime = now.timestamp()
            tar.addfile(info, BytesIO(data))

    archive_size = dest.stat().st_size

    # Parse flows for tab_summary and changes_summary
    try:
        current_parsed = parse_flows(json.loads(flows_bytes))
    except Exception:
        logger.warning(
            "Failed to parse imported flows for summaries;"
            " continuing with empty summaries",
            exc_info=True,
        )
        current_parsed = None

    tab_summary = [t["label"] for t in current_parsed["tabs"]] if current_parsed else []

    # Compute changes against last backup
    last = (
        BackupRecord.objects.filter(config=config, status="success")
        .order_by("-created_at")
        .first()
    )
    changes_summary = _compute_changes(last, current_parsed)

    record = BackupRecord.objects.create(
        config=config,
        created_at=now,
        filename=filename,
        file_path=str(dest),
        file_size=archive_size,
        checksum=checksum,
        status="success",
        trigger="import",
        label=label or "",
        notes=notes or "",
        tab_summary=tab_summary,
        changes_summary=changes_summary,
        includes_credentials="flows_cred.json" in contents,
        includes_settings="settings.js" in contents,
    )

    config.last_successful_backup = now
    config.last_backup_error = ""
    config.save(update_fields=["last_successful_backup", "last_backup_error"])

    logger.info("Backup imported: %s (%d bytes)", filename, archive_size)

    _notify_import(config, record)

    try:
        from backup.services.retention_service import apply_retention

        apply_retention(config)
    except Exception:
        logger.warning("Retention cleanup failed after import", exc_info=True)

    return record, duplicate_warning


def _compute_changes(last_backup, current_parsed):
    """Compare current parsed flows against the previous backup's flows."""
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


def _notify_import(config, record):
    """Send success notification for an import."""
    try:
        from backup.services.notification_service import notify
        from backup.services.notifications.base import NotificationPayload, NotifyEvent

        payload = NotificationPayload(
            event=NotifyEvent.IMPORT_SUCCESS,
            instance_name=config.name,
            instance_slug=config.slug,
            instance_color=config.color,
            title=f"Backup import successful \u2014 {config.name}",
            message=record.filename,
            filename=record.filename,
            file_size=record.file_size,
            trigger="import",
        )
        notify(config, payload)
    except Exception:
        logger.warning("Notification failed after import", exc_info=True)
