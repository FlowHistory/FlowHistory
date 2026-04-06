"""Base classes and constants for the notification system."""

import abc
from dataclasses import dataclass
from typing import Optional


class NotifyEvent:
    """Notification event type constants."""

    BACKUP_SUCCESS = "backup_success"
    BACKUP_FAILED = "backup_failed"
    RESTORE_SUCCESS = "restore_success"
    RESTORE_FAILED = "restore_failed"
    RETENTION_CLEANUP = "retention_cleanup"

    ALL = {
        BACKUP_SUCCESS, BACKUP_FAILED,
        RESTORE_SUCCESS, RESTORE_FAILED,
        RETENTION_CLEANUP,
    }

    DEFAULT = {BACKUP_FAILED, RESTORE_SUCCESS, RESTORE_FAILED}


@dataclass
class NotificationPayload:
    """Structured data for a notification event."""

    event: str
    instance_name: str
    instance_slug: str
    instance_color: str
    title: str
    message: str
    error: Optional[str] = None
    filename: Optional[str] = None
    file_size: Optional[int] = None
    trigger: Optional[str] = None


class NotificationBackend(abc.ABC):
    """Abstract base for notification delivery backends."""

    @abc.abstractmethod
    def name(self) -> str:
        """Human-readable backend name (e.g., 'Discord')."""
        ...

    @abc.abstractmethod
    def is_configured(self, config) -> bool:
        """Return True if this backend has credentials for the given instance.

        Should check per-instance env var first, then global fallback.
        """
        ...

    @abc.abstractmethod
    def send(self, config, payload: NotificationPayload) -> None:
        """Deliver a notification using this instance's config.

        Should log errors internally. Callers catch exceptions as a safety net.
        """
        ...
