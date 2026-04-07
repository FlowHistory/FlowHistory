"""Delete old backups by count and age retention policies."""

import logging
from datetime import timedelta
from pathlib import Path

from django.utils import timezone

from backup.models import BackupRecord

logger = logging.getLogger(__name__)


def apply_retention(config):
    """Delete backups exceeding max_backups count and max_age_days age.

    Args:
        config: NodeRedConfig instance.

    Returns:
        {"deleted_by_count": int, "deleted_by_age": int, "errors": [str]}
    """

    errors = []
    now = timezone.now()

    # Never delete recent pre_restore safety backups (< 24h old)
    protected_cutoff = now - timedelta(hours=24)

    # --- Delete by age first ---
    age_cutoff = now - timedelta(days=config.max_age_days)
    old_backups = (
        BackupRecord.objects.filter(
            config=config, status="success", created_at__lt=age_cutoff
        )
        .exclude(trigger="pre_restore", created_at__gte=protected_cutoff)
        .exclude(is_pinned=True)
    )

    deleted_by_age = 0
    for record in old_backups:
        error = _delete_backup(record)
        if error:
            errors.append(error)
        else:
            deleted_by_age += 1

    # --- Delete by count ---
    remaining = BackupRecord.objects.filter(config=config, status="success").order_by(
        "-created_at"
    )

    excess = list(remaining[config.max_backups :])
    # Filter out protected pre_restore backups
    excess = [
        r
        for r in excess
        if not (r.trigger == "pre_restore" and r.created_at >= protected_cutoff)
        and not r.is_pinned
    ]

    deleted_by_count = 0
    for record in excess:
        error = _delete_backup(record)
        if error:
            errors.append(error)
        else:
            deleted_by_count += 1

    if deleted_by_age or deleted_by_count:
        logger.info(
            "Retention cleanup: %d by age, %d by count",
            deleted_by_age,
            deleted_by_count,
        )
        _notify_retention(config, deleted_by_age, deleted_by_count)

    return {
        "deleted_by_count": deleted_by_count,
        "deleted_by_age": deleted_by_age,
        "errors": errors,
    }


def _notify_retention(config, deleted_by_age, deleted_by_count):
    """Send notification for retention cleanup."""
    try:
        from backup.services.notification_service import notify
        from backup.services.notifications.base import NotificationPayload, NotifyEvent

        total = deleted_by_age + deleted_by_count
        payload = NotificationPayload(
            event=NotifyEvent.RETENTION_CLEANUP,
            instance_name=config.name,
            instance_slug=config.slug,
            instance_color=config.color,
            title=f"Retention cleanup \u2014 {config.name}",
            message=(
                f"Deleted {total} backup{'s' if total != 1 else ''}"
                f" ({deleted_by_age} by age,"
                f" {deleted_by_count} by count)."
            ),
        )
        notify(config, payload)
    except Exception:
        logger.warning("Notification failed after retention cleanup", exc_info=True)


def _delete_backup(record):
    """Delete a backup record and its archive file. Returns error string or None."""
    try:
        path = Path(record.file_path)
        path.unlink(missing_ok=True)
        record.delete()
        logger.debug("Deleted backup: %s", record.filename)
        return None
    except Exception as e:
        msg = f"Failed to delete {record.filename}: {e}"
        logger.error(msg)
        return msg
