"""Discord webhook notification backend."""

import json
import logging
from urllib.error import URLError
from urllib.request import Request, urlopen

from backup.services.notifications.base import NotificationBackend, NotificationPayload, NotifyEvent

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10

EVENT_COLORS = {
    NotifyEvent.BACKUP_SUCCESS: 0x10B981,     # green
    NotifyEvent.BACKUP_FAILED: 0xEF4444,      # red
    NotifyEvent.RESTORE_SUCCESS: 0x3B82F6,    # blue
    NotifyEvent.RESTORE_FAILED: 0xEF4444,     # red
    NotifyEvent.RETENTION_CLEANUP: 0xF59E0B,  # amber
}

EVENT_EMOJI = {
    NotifyEvent.BACKUP_SUCCESS: "\u2705",     # check mark
    NotifyEvent.BACKUP_FAILED: "\u274c",      # cross mark
    NotifyEvent.RESTORE_SUCCESS: "\u2705",
    NotifyEvent.RESTORE_FAILED: "\u274c",
    NotifyEvent.RETENTION_CLEANUP: "\U0001f9f9",  # broom
}


class DiscordBackend(NotificationBackend):

    def name(self):
        return "Discord"

    def is_configured(self, config):
        return bool(config.get_notification_url("DISCORD_WEBHOOK_URL"))

    def send(self, config, payload: NotificationPayload):
        webhook_url = config.get_notification_url("DISCORD_WEBHOOK_URL")
        if not webhook_url:
            return

        emoji = EVENT_EMOJI.get(payload.event, "")
        embed = {
            "title": f"{emoji} {payload.title}" if emoji else payload.title,
            "description": payload.message,
            "color": EVENT_COLORS.get(payload.event, 0x6B7280),
            "footer": {"text": f"FlowHistory \u2014 {payload.instance_name}"},
        }

        fields = []
        if payload.trigger:
            fields.append({"name": "Trigger", "value": payload.trigger, "inline": True})
        if payload.filename:
            fields.append({"name": "File", "value": payload.filename, "inline": True})
        if payload.file_size is not None:
            size_kb = payload.file_size / 1024
            fields.append({"name": "Size", "value": f"{size_kb:.1f} KB", "inline": True})
        if payload.error:
            fields.append({"name": "Error", "value": f"```{payload.error[:1000]}```", "inline": False})
        if fields:
            embed["fields"] = fields

        body = json.dumps({"embeds": [embed]}).encode()
        req = Request(webhook_url, data=body, headers={"Content-Type": "application/json"})

        try:
            with urlopen(req, timeout=TIMEOUT_SECONDS) as response:
                response.read()
        except (URLError, OSError, ValueError) as exc:
            logger.warning("Discord webhook failed: %s", exc)
