"""Telegram Bot API notification backend."""

import json
import logging
import re
from urllib.error import URLError
from urllib.request import Request, urlopen

from backup.services.notifications.base import (
    EVENT_EMOJI,
    NotificationBackend,
    NotificationPayload,
)

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10

# Characters that must be escaped in MarkdownV2
_ESCAPE_RE = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def _escape(text):
    """Escape special characters for Telegram MarkdownV2."""
    return _ESCAPE_RE.sub(r"\\\1", str(text))


def _escape_pre(text):
    """Escape chars inside a MarkdownV2 pre block.

    Only ` and \\ need escaping.
    """
    return str(text).replace("\\", "\\\\").replace("`", "\\`")


class TelegramBackend(NotificationBackend):
    def name(self):
        return "Telegram"

    def is_configured(self, config):
        return bool(
            config.get_notification_url("TELEGRAM_BOT_TOKEN")
            and config.get_notification_url("TELEGRAM_CHAT_ID")
        )

    def send(self, config, payload: NotificationPayload):
        bot_token = config.get_notification_url("TELEGRAM_BOT_TOKEN")
        chat_id = config.get_notification_url("TELEGRAM_CHAT_ID")
        if not bot_token or not chat_id:
            return

        emoji = EVENT_EMOJI.get(payload.event, "")
        lines = [
            f"*{emoji} {_escape(payload.title)}*"
            if emoji
            else f"*{_escape(payload.title)}*"
        ]
        lines.append(_escape(payload.message))

        if payload.trigger:
            lines.append(f"*Trigger:* {_escape(payload.trigger)}")
        if payload.filename:
            lines.append(f"*File:* {_escape(payload.filename)}")
        if payload.file_size is not None:
            size_kb = payload.file_size / 1024
            lines.append(f"*Size:* {_escape(f'{size_kb:.1f} KB')}")
        if payload.error:
            lines.append(f"```\n{_escape_pre(payload.error[:1000])}\n```")

        lines.append(f"\n_{_escape(f'FlowHistory \u2014 {payload.instance_name}')}_")

        text = "\n".join(lines)
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        body = json.dumps(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "MarkdownV2",
            }
        ).encode()
        req = Request(url, data=body, headers={"Content-Type": "application/json"})

        try:
            with urlopen(req, timeout=TIMEOUT_SECONDS) as response:
                response.read()
        except (URLError, OSError, ValueError) as exc:
            logger.warning("Telegram API failed: %s", exc)
