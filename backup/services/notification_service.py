"""Dispatch notification events to all configured backends."""

import logging

from backup.services.notifications.base import NotifyEvent

logger = logging.getLogger(__name__)

_backends = None


def _discover_backends():
    """Import and instantiate all backend classes."""
    from backup.services.notifications.discord import DiscordBackend
    from backup.services.notifications.slack import SlackBackend
    from backup.services.notifications.telegram import TelegramBackend
    from backup.services.notifications.pushbullet import PushbulletBackend
    from backup.services.notifications.homeassistant import HomeAssistantBackend

    return [
        DiscordBackend(),
        SlackBackend(),
        TelegramBackend(),
        PushbulletBackend(),
        HomeAssistantBackend(),
    ]


def _get_backends():
    """Return the list of all known backends (lazy-initialized)."""
    global _backends
    if _backends is None:
        _backends = _discover_backends()
    return _backends


def _get_instance_events(config):
    """Return the set of events this instance wants notifications for."""
    raw = getattr(config, "notify_events", "") or ""
    raw = raw.strip()
    if not raw:
        return NotifyEvent.DEFAULT
    if raw.lower() == "none":
        return set()
    if raw.lower() == "all":
        return NotifyEvent.ALL
    events = {e.strip() for e in raw.split(",") if e.strip()}
    valid = events & NotifyEvent.ALL
    if valid != events:
        unknown = events - NotifyEvent.ALL
        logger.warning("Unknown notify events for %s: %s", config.name, unknown)
    return valid or NotifyEvent.DEFAULT


def get_configured_backends(config):
    """Return list of backend names configured for this instance."""
    return [b.name() for b in _get_backends() if b.is_configured(config)]


def get_configured_backends_objects(config):
    """Return list of backend instances configured for this instance."""
    return [b for b in _get_backends() if b.is_configured(config)]


def notify(config, payload):
    """Send a notification to all configured backends if the event is enabled.

    Args:
        config: NodeRedConfig instance.
        payload: NotificationPayload with event details.
    """
    if not getattr(config, "notify_enabled", True):
        return

    enabled_events = _get_instance_events(config)
    if payload.event not in enabled_events:
        return

    for backend in _get_backends():
        if backend.is_configured(config):
            try:
                backend.send(config, payload)
            except Exception:
                logger.warning(
                    "Notification backend %s failed for event %s",
                    backend.name(), payload.event,
                    exc_info=True,
                )
