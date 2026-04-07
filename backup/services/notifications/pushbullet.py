"""Pushbullet notification backend."""

import json
import logging
from urllib.error import URLError
from urllib.request import Request, urlopen

from backup.services.notifications.base import (
    EVENT_EMOJI,
    NotificationBackend,
    NotificationPayload,
)

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10


class PushbulletBackend(NotificationBackend):
    def name(self):
        return "Pushbullet"

    def is_configured(self, config):
        return bool(config.get_notification_url("PUSHBULLET_API_KEY"))

    def send(self, config, payload: NotificationPayload):
        api_key = config.get_notification_url("PUSHBULLET_API_KEY")
        if not api_key:
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
        lines.append(f"\nFlowHistory \u2014 {payload.instance_name}")

        body = json.dumps(
            {
                "type": "note",
                "title": title,
                "body": "\n".join(lines),
            }
        ).encode()
        req = Request(
            "https://api.pushbullet.com/v2/pushes",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Access-Token": api_key,
            },
        )

        try:
            with urlopen(req, timeout=TIMEOUT_SECONDS) as response:
                response.read()
        except (URLError, OSError, ValueError) as exc:
            logger.warning("Pushbullet API failed: %s", exc)
