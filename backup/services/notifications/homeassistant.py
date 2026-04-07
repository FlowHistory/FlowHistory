"""Home Assistant notification backend."""

import json
import logging
from urllib.error import URLError
from urllib.request import Request, urlopen

from backup.services.notifications.base import (
    EVENT_EMOJI, NotificationBackend, NotificationPayload, NotifyEvent,
)

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10


class HomeAssistantBackend(NotificationBackend):

    def name(self):
        return "Home Assistant"

    def is_configured(self, config):
        return bool(
            config.get_notification_url("HOMEASSISTANT_URL")
            and config.get_notification_url("HOMEASSISTANT_TOKEN")
        )

    def send(self, config, payload: NotificationPayload):
        ha_url = config.get_notification_url("HOMEASSISTANT_URL")
        ha_token = config.get_notification_url("HOMEASSISTANT_TOKEN")
        if not ha_url or not ha_token:
            return

        emoji = EVENT_EMOJI.get(payload.event, "")
        title = f"{emoji} {payload.title}" if emoji else payload.title

        lines = [payload.message]
        if payload.trigger:
            lines.append(f"Trigger: {payload.trigger}")
        if payload.filename:
            lines.append(f"File: {payload.filename}")
        if payload.file_size is not None:
            size_kb = payload.file_size / 1024
            lines.append(f"Size: {size_kb:.1f} KB")
        if payload.error:
            lines.append(f"Error: {payload.error[:1000]}")

        notification_id = f"flowhistory_{payload.instance_slug}_{payload.event}"

        url = f"{ha_url.rstrip('/')}/api/services/persistent_notification/create"
        body = json.dumps({
            "title": title,
            "message": "\n".join(lines),
            "notification_id": notification_id,
        }).encode()
        req = Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {ha_token}",
            },
        )

        try:
            with urlopen(req, timeout=TIMEOUT_SECONDS) as response:
                response.read()
        except (URLError, OSError, ValueError) as exc:
            logger.warning("Home Assistant API failed: %s", exc)
