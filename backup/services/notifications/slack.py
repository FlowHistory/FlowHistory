"""Slack webhook notification backend."""

import json
import logging
from urllib.error import URLError
from urllib.request import Request, urlopen

from backup.services.notifications.base import (
    EVENT_COLORS,
    EVENT_EMOJI,
    NotificationBackend,
    NotificationPayload,
)

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10


class SlackBackend(NotificationBackend):
    def name(self):
        return "Slack"

    def is_configured(self, config):
        return bool(config.get_notification_url("SLACK_WEBHOOK_URL"))

    def send(self, config, payload: NotificationPayload):
        webhook_url = config.get_notification_url("SLACK_WEBHOOK_URL")
        if not webhook_url:
            return

        emoji = EVENT_EMOJI.get(payload.event, "")
        color = EVENT_COLORS.get(payload.event, "#6B7280")

        fields = []
        if payload.trigger:
            fields.append({"title": "Trigger", "value": payload.trigger, "short": True})
        if payload.filename:
            fields.append({"title": "File", "value": payload.filename, "short": True})
        if payload.file_size is not None:
            size_kb = payload.file_size / 1024
            fields.append(
                {"title": "Size", "value": f"{size_kb:.1f} KB", "short": True}
            )

        attachment = {
            "color": color,
            "pretext": f"{emoji} {payload.title}" if emoji else payload.title,
            "text": payload.message,
            "footer": f"FlowHistory \u2014 {payload.instance_name}",
        }
        if fields:
            attachment["fields"] = fields
        if payload.error:
            attachment["text"] += f"\n```{payload.error[:1000]}```"

        body = json.dumps({"attachments": [attachment]}).encode()
        req = Request(
            webhook_url, data=body, headers={"Content-Type": "application/json"}
        )

        try:
            with urlopen(req, timeout=TIMEOUT_SECONDS) as response:
                response.read()
        except (URLError, OSError, ValueError) as exc:
            logger.warning("Slack webhook failed: %s", exc)
