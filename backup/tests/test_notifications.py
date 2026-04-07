import json
import os
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from backup.models import NodeRedConfig
from backup.services.backup_service import create_backup
from backup.services.notification_service import (
    _get_instance_events,
    notify,
)
from backup.services.notifications.base import (
    NotificationBackend,
    NotificationPayload,
    NotifyEvent,
)
from backup.services.restore_service import restore_backup
from backup.tests.helpers import SAMPLE_FLOWS, TempBackupDirMixin


class NotifyEventTest(TestCase):
    def test_all_contains_every_event(self):
        expected = {
            "backup_success",
            "backup_failed",
            "restore_success",
            "restore_failed",
            "retention_cleanup",
        }
        self.assertEqual(NotifyEvent.ALL, expected)

    def test_default_is_subset_of_all(self):
        self.assertTrue(NotifyEvent.DEFAULT.issubset(NotifyEvent.ALL))

    def test_default_events(self):
        self.assertEqual(
            NotifyEvent.DEFAULT,
            {"backup_failed", "restore_success", "restore_failed"},
        )


class GetInstanceEventsTest(TestCase):
    def setUp(self):
        self.config = NodeRedConfig.objects.create(name="Test")

    def test_empty_returns_defaults(self):
        self.config.notify_events = ""
        self.assertEqual(_get_instance_events(self.config), NotifyEvent.DEFAULT)

    def test_none_returns_empty_set(self):
        self.config.notify_events = "none"
        self.assertEqual(_get_instance_events(self.config), set())

    def test_all_returns_all_events(self):
        self.config.notify_events = "all"
        self.assertEqual(_get_instance_events(self.config), NotifyEvent.ALL)

    def test_comma_separated(self):
        self.config.notify_events = "backup_failed,restore_failed"
        result = _get_instance_events(self.config)
        self.assertEqual(result, {"backup_failed", "restore_failed"})

    def test_unknown_events_ignored(self):
        self.config.notify_events = "backup_failed,bogus_event"
        result = _get_instance_events(self.config)
        self.assertEqual(result, {"backup_failed"})

    def test_all_unknown_falls_back_to_defaults(self):
        self.config.notify_events = "bogus"
        result = _get_instance_events(self.config)
        self.assertEqual(result, NotifyEvent.DEFAULT)


class NotificationPayloadTest(TestCase):
    def test_payload_creation(self):
        p = NotificationPayload(
            event=NotifyEvent.BACKUP_SUCCESS,
            instance_name="Test",
            instance_slug="test",
            instance_color="#3B82F6",
            title="Backup successful",
            message="Created file.tar.gz",
            filename="file.tar.gz",
            file_size=1024,
            trigger="manual",
        )
        self.assertEqual(p.event, "backup_success")
        self.assertEqual(p.filename, "file.tar.gz")
        self.assertIsNone(p.error)

    def test_payload_defaults(self):
        p = NotificationPayload(
            event=NotifyEvent.BACKUP_FAILED,
            instance_name="X",
            instance_slug="x",
            instance_color="#EF4444",
            title="Failed",
            message="Oops",
        )
        self.assertIsNone(p.error)
        self.assertIsNone(p.filename)
        self.assertIsNone(p.file_size)
        self.assertIsNone(p.trigger)


class GetNotificationUrlTest(TestCase):
    def setUp(self):
        self.config = NodeRedConfig.objects.create(name="Prod", env_prefix="PROD")

    @patch.dict(
        os.environ, {"FLOWHISTORY_PROD_DISCORD_WEBHOOK_URL": "https://instance.url"}
    )
    def test_instance_url_takes_priority(self):
        self.assertEqual(
            self.config.get_notification_url("DISCORD_WEBHOOK_URL"),
            "https://instance.url",
        )

    @patch.dict(
        os.environ,
        {"FLOWHISTORY_NOTIFY_DISCORD_WEBHOOK_URL": "https://global.url"},
        clear=False,
    )
    def test_global_fallback(self):
        self.assertEqual(
            self.config.get_notification_url("DISCORD_WEBHOOK_URL"),
            "https://global.url",
        )

    @patch.dict(
        os.environ,
        {
            "FLOWHISTORY_PROD_DISCORD_WEBHOOK_URL": "https://instance.url",
            "FLOWHISTORY_NOTIFY_DISCORD_WEBHOOK_URL": "https://global.url",
        },
    )
    def test_instance_overrides_global(self):
        self.assertEqual(
            self.config.get_notification_url("DISCORD_WEBHOOK_URL"),
            "https://instance.url",
        )

    @patch.dict(os.environ, {}, clear=True)
    def test_no_env_returns_empty(self):
        self.assertEqual(self.config.get_notification_url("DISCORD_WEBHOOK_URL"), "")

    def test_no_prefix_uses_global_only(self):
        config = NodeRedConfig.objects.create(name="NoPfx", env_prefix="")
        with patch.dict(
            os.environ, {"FLOWHISTORY_NOTIFY_DISCORD_WEBHOOK_URL": "https://global.url"}
        ):
            self.assertEqual(
                config.get_notification_url("DISCORD_WEBHOOK_URL"), "https://global.url"
            )


class NotifyDispatcherTest(TestCase):
    def setUp(self):
        self.config = NodeRedConfig.objects.create(name="Test", notify_enabled=True)
        self.payload = NotificationPayload(
            event=NotifyEvent.BACKUP_FAILED,
            instance_name="Test",
            instance_slug="test",
            instance_color="#EF4444",
            title="Backup failed",
            message="Error occurred",
        )

    @patch("backup.services.notification_service._get_backends")
    def test_notify_dispatches_to_configured_backend(self, mock_get):
        mock_backend = MagicMock(spec=NotificationBackend)
        mock_backend.is_configured.return_value = True
        mock_get.return_value = [mock_backend]

        notify(self.config, self.payload)

        mock_backend.is_configured.assert_called_once_with(self.config)
        mock_backend.send.assert_called_once_with(self.config, self.payload)

    @patch("backup.services.notification_service._get_backends")
    def test_notify_skips_unconfigured_backend(self, mock_get):
        mock_backend = MagicMock(spec=NotificationBackend)
        mock_backend.is_configured.return_value = False
        mock_get.return_value = [mock_backend]

        notify(self.config, self.payload)

        mock_backend.send.assert_not_called()

    @patch("backup.services.notification_service._get_backends")
    def test_notify_skips_when_disabled(self, mock_get):
        mock_backend = MagicMock(spec=NotificationBackend)
        mock_get.return_value = [mock_backend]
        self.config.notify_enabled = False

        notify(self.config, self.payload)

        mock_backend.is_configured.assert_not_called()
        mock_backend.send.assert_not_called()

    @patch("backup.services.notification_service._get_backends")
    def test_notify_skips_event_not_in_enabled_set(self, mock_get):
        mock_backend = MagicMock(spec=NotificationBackend)
        mock_get.return_value = [mock_backend]
        self.config.notify_events = "restore_success"

        payload = NotificationPayload(
            event=NotifyEvent.BACKUP_SUCCESS,
            instance_name="Test",
            instance_slug="test",
            instance_color="#10B981",
            title="Backup ok",
            message="ok",
        )
        notify(self.config, payload)

        mock_backend.send.assert_not_called()

    @patch("backup.services.notification_service._get_backends")
    def test_notify_catches_backend_exception(self, mock_get):
        mock_backend = MagicMock(spec=NotificationBackend)
        mock_backend.is_configured.return_value = True
        mock_backend.send.side_effect = Exception("Network error")
        mock_backend.name.return_value = "TestBackend"
        mock_get.return_value = [mock_backend]

        # Should not raise
        notify(self.config, self.payload)


class DiscordBackendTest(TestCase):
    def setUp(self):
        self.config = NodeRedConfig.objects.create(name="Test", env_prefix="TEST")

    @patch.dict(
        os.environ,
        {"FLOWHISTORY_TEST_DISCORD_WEBHOOK_URL": "https://discord.test/webhook"},
    )
    def test_is_configured_with_instance_url(self):
        from backup.services.notifications.discord import DiscordBackend

        backend = DiscordBackend()
        self.assertTrue(backend.is_configured(self.config))

    @patch.dict(os.environ, {}, clear=True)
    def test_is_not_configured_without_url(self):
        from backup.services.notifications.discord import DiscordBackend

        backend = DiscordBackend()
        self.assertFalse(backend.is_configured(self.config))

    @patch("backup.services.notifications.discord.urlopen")
    @patch.dict(
        os.environ,
        {"FLOWHISTORY_TEST_DISCORD_WEBHOOK_URL": "https://discord.test/webhook"},
    )
    def test_send_posts_to_webhook(self, mock_urlopen):
        from backup.services.notifications.discord import DiscordBackend

        backend = DiscordBackend()
        payload = NotificationPayload(
            event=NotifyEvent.BACKUP_SUCCESS,
            instance_name="Test",
            instance_slug="test",
            instance_color="#10B981",
            title="Backup successful",
            message="Created test.tar.gz",
            filename="test.tar.gz",
            file_size=2048,
            trigger="manual",
        )
        backend.send(self.config, payload)

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "https://discord.test/webhook")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        body = json.loads(req.data)
        self.assertIn("embeds", body)
        embed = body["embeds"][0]
        self.assertIn("Backup successful", embed["title"])
        self.assertEqual(embed["color"], 0x10B981)
        self.assertEqual(len(embed["fields"]), 3)  # trigger, filename, size

    @patch("backup.services.notifications.discord.urlopen")
    @patch.dict(
        os.environ,
        {"FLOWHISTORY_TEST_DISCORD_WEBHOOK_URL": "https://discord.test/webhook"},
    )
    def test_send_includes_error_field(self, mock_urlopen):
        from backup.services.notifications.discord import DiscordBackend

        backend = DiscordBackend()
        payload = NotificationPayload(
            event=NotifyEvent.BACKUP_FAILED,
            instance_name="Test",
            instance_slug="test",
            instance_color="#EF4444",
            title="Backup failed",
            message="Failed",
            error="File not found",
            trigger="scheduled",
        )
        backend.send(self.config, payload)

        body = json.loads(mock_urlopen.call_args[0][0].data)
        fields = body["embeds"][0]["fields"]
        error_field = [f for f in fields if f["name"] == "Error"][0]
        self.assertIn("File not found", error_field["value"])

    @patch("backup.services.notifications.discord.urlopen")
    @patch.dict(
        os.environ,
        {"FLOWHISTORY_TEST_DISCORD_WEBHOOK_URL": "https://discord.test/webhook"},
    )
    def test_send_handles_urlopen_failure(self, mock_urlopen):
        from urllib.error import URLError

        from backup.services.notifications.discord import DiscordBackend

        mock_urlopen.side_effect = URLError("Connection refused")
        backend = DiscordBackend()
        payload = NotificationPayload(
            event=NotifyEvent.BACKUP_FAILED,
            instance_name="Test",
            instance_slug="test",
            instance_color="#EF4444",
            title="Failed",
            message="Failed",
        )
        # Should not raise
        backend.send(self.config, payload)


class SlackBackendTest(TestCase):
    def setUp(self):
        self.config = NodeRedConfig.objects.create(name="Test", env_prefix="TEST")

    @patch.dict(
        os.environ,
        {"FLOWHISTORY_TEST_SLACK_WEBHOOK_URL": "https://hooks.slack.com/test"},
    )
    def test_is_configured_with_instance_url(self):
        from backup.services.notifications.slack import SlackBackend

        backend = SlackBackend()
        self.assertTrue(backend.is_configured(self.config))

    @patch.dict(os.environ, {}, clear=True)
    def test_is_not_configured_without_url(self):
        from backup.services.notifications.slack import SlackBackend

        backend = SlackBackend()
        self.assertFalse(backend.is_configured(self.config))

    @patch("backup.services.notifications.slack.urlopen")
    @patch.dict(
        os.environ,
        {"FLOWHISTORY_TEST_SLACK_WEBHOOK_URL": "https://hooks.slack.com/test"},
    )
    def test_send_posts_to_webhook(self, mock_urlopen):
        from backup.services.notifications.slack import SlackBackend

        backend = SlackBackend()
        payload = NotificationPayload(
            event=NotifyEvent.BACKUP_SUCCESS,
            instance_name="Test",
            instance_slug="test",
            instance_color="#10B981",
            title="Backup successful",
            message="Created test.tar.gz",
            filename="test.tar.gz",
            file_size=2048,
            trigger="manual",
        )
        backend.send(self.config, payload)

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "https://hooks.slack.com/test")
        body = json.loads(req.data)
        self.assertIn("attachments", body)
        attachment = body["attachments"][0]
        self.assertIn("Backup successful", attachment["pretext"])
        self.assertEqual(attachment["color"], "#10B981")
        self.assertEqual(len(attachment["fields"]), 3)

    @patch("backup.services.notifications.slack.urlopen")
    @patch.dict(
        os.environ,
        {"FLOWHISTORY_TEST_SLACK_WEBHOOK_URL": "https://hooks.slack.com/test"},
    )
    def test_send_handles_failure(self, mock_urlopen):
        from urllib.error import URLError

        from backup.services.notifications.slack import SlackBackend

        mock_urlopen.side_effect = URLError("Connection refused")
        backend = SlackBackend()
        payload = NotificationPayload(
            event=NotifyEvent.BACKUP_FAILED,
            instance_name="Test",
            instance_slug="test",
            instance_color="#EF4444",
            title="Failed",
            message="Failed",
        )
        backend.send(self.config, payload)


class TelegramBackendTest(TestCase):
    def setUp(self):
        self.config = NodeRedConfig.objects.create(name="Test", env_prefix="TEST")

    @patch.dict(
        os.environ,
        {
            "FLOWHISTORY_TEST_TELEGRAM_BOT_TOKEN": "123:ABC",
            "FLOWHISTORY_TEST_TELEGRAM_CHAT_ID": "456",
        },
    )
    def test_is_configured_with_both_fields(self):
        from backup.services.notifications.telegram import TelegramBackend

        backend = TelegramBackend()
        self.assertTrue(backend.is_configured(self.config))

    @patch.dict(os.environ, {"FLOWHISTORY_TEST_TELEGRAM_BOT_TOKEN": "123:ABC"})
    def test_is_not_configured_without_chat_id(self):
        from backup.services.notifications.telegram import TelegramBackend

        backend = TelegramBackend()
        self.assertFalse(backend.is_configured(self.config))

    @patch.dict(os.environ, {}, clear=True)
    def test_is_not_configured_without_any(self):
        from backup.services.notifications.telegram import TelegramBackend

        backend = TelegramBackend()
        self.assertFalse(backend.is_configured(self.config))

    @patch("backup.services.notifications.telegram.urlopen")
    @patch.dict(
        os.environ,
        {
            "FLOWHISTORY_TEST_TELEGRAM_BOT_TOKEN": "123:ABC",
            "FLOWHISTORY_TEST_TELEGRAM_CHAT_ID": "456",
        },
    )
    def test_send_posts_to_api(self, mock_urlopen):
        from backup.services.notifications.telegram import TelegramBackend

        backend = TelegramBackend()
        payload = NotificationPayload(
            event=NotifyEvent.BACKUP_SUCCESS,
            instance_name="Test",
            instance_slug="test",
            instance_color="#10B981",
            title="Backup successful",
            message="Created test.tar.gz",
            filename="test.tar.gz",
            file_size=2048,
            trigger="manual",
        )
        backend.send(self.config, payload)

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(
            req.full_url, "https://api.telegram.org/bot123:ABC/sendMessage"
        )
        body = json.loads(req.data)
        self.assertEqual(body["chat_id"], "456")
        self.assertEqual(body["parse_mode"], "MarkdownV2")
        self.assertIn("Backup successful", body["text"])

    @patch("backup.services.notifications.telegram.urlopen")
    @patch.dict(
        os.environ,
        {
            "FLOWHISTORY_TEST_TELEGRAM_BOT_TOKEN": "123:ABC",
            "FLOWHISTORY_TEST_TELEGRAM_CHAT_ID": "456",
        },
    )
    def test_send_handles_failure(self, mock_urlopen):
        from urllib.error import URLError

        from backup.services.notifications.telegram import TelegramBackend

        mock_urlopen.side_effect = URLError("Connection refused")
        backend = TelegramBackend()
        payload = NotificationPayload(
            event=NotifyEvent.BACKUP_FAILED,
            instance_name="Test",
            instance_slug="test",
            instance_color="#EF4444",
            title="Failed",
            message="Failed",
        )
        backend.send(self.config, payload)


class PushbulletBackendTest(TestCase):
    def setUp(self):
        self.config = NodeRedConfig.objects.create(name="Test", env_prefix="TEST")

    @patch.dict(os.environ, {"FLOWHISTORY_TEST_PUSHBULLET_API_KEY": "o.abc123"})
    def test_is_configured_with_api_key(self):
        from backup.services.notifications.pushbullet import PushbulletBackend

        backend = PushbulletBackend()
        self.assertTrue(backend.is_configured(self.config))

    @patch.dict(os.environ, {}, clear=True)
    def test_is_not_configured_without_key(self):
        from backup.services.notifications.pushbullet import PushbulletBackend

        backend = PushbulletBackend()
        self.assertFalse(backend.is_configured(self.config))

    @patch("backup.services.notifications.pushbullet.urlopen")
    @patch.dict(os.environ, {"FLOWHISTORY_TEST_PUSHBULLET_API_KEY": "o.abc123"})
    def test_send_posts_to_api(self, mock_urlopen):
        from backup.services.notifications.pushbullet import PushbulletBackend

        backend = PushbulletBackend()
        payload = NotificationPayload(
            event=NotifyEvent.BACKUP_SUCCESS,
            instance_name="Test",
            instance_slug="test",
            instance_color="#10B981",
            title="Backup successful",
            message="Created test.tar.gz",
            filename="test.tar.gz",
            file_size=2048,
            trigger="manual",
        )
        backend.send(self.config, payload)

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "https://api.pushbullet.com/v2/pushes")
        self.assertEqual(req.get_header("Access-token"), "o.abc123")
        body = json.loads(req.data)
        self.assertEqual(body["type"], "note")
        self.assertIn("Backup successful", body["title"])
        self.assertIn("Created test.tar.gz", body["body"])

    @patch("backup.services.notifications.pushbullet.urlopen")
    @patch.dict(os.environ, {"FLOWHISTORY_TEST_PUSHBULLET_API_KEY": "o.abc123"})
    def test_send_handles_failure(self, mock_urlopen):
        from urllib.error import URLError

        from backup.services.notifications.pushbullet import PushbulletBackend

        mock_urlopen.side_effect = URLError("Connection refused")
        backend = PushbulletBackend()
        payload = NotificationPayload(
            event=NotifyEvent.BACKUP_FAILED,
            instance_name="Test",
            instance_slug="test",
            instance_color="#EF4444",
            title="Failed",
            message="Failed",
        )
        backend.send(self.config, payload)


class HomeAssistantBackendTest(TestCase):
    def setUp(self):
        self.config = NodeRedConfig.objects.create(name="Test", env_prefix="TEST")

    @patch.dict(
        os.environ,
        {
            "FLOWHISTORY_TEST_HOMEASSISTANT_URL": "http://ha.local:8123",
            "FLOWHISTORY_TEST_HOMEASSISTANT_TOKEN": "eyJtoken",
        },
    )
    def test_is_configured_with_both_fields(self):
        from backup.services.notifications.homeassistant import HomeAssistantBackend

        backend = HomeAssistantBackend()
        self.assertTrue(backend.is_configured(self.config))

    @patch.dict(
        os.environ, {"FLOWHISTORY_TEST_HOMEASSISTANT_URL": "http://ha.local:8123"}
    )
    def test_is_not_configured_without_token(self):
        from backup.services.notifications.homeassistant import HomeAssistantBackend

        backend = HomeAssistantBackend()
        self.assertFalse(backend.is_configured(self.config))

    @patch.dict(os.environ, {}, clear=True)
    def test_is_not_configured_without_any(self):
        from backup.services.notifications.homeassistant import HomeAssistantBackend

        backend = HomeAssistantBackend()
        self.assertFalse(backend.is_configured(self.config))

    @patch("backup.services.notifications.homeassistant.urlopen")
    @patch.dict(
        os.environ,
        {
            "FLOWHISTORY_TEST_HOMEASSISTANT_URL": "http://ha.local:8123",
            "FLOWHISTORY_TEST_HOMEASSISTANT_TOKEN": "eyJtoken",
        },
    )
    def test_send_posts_to_api(self, mock_urlopen):
        from backup.services.notifications.homeassistant import HomeAssistantBackend

        backend = HomeAssistantBackend()
        payload = NotificationPayload(
            event=NotifyEvent.BACKUP_SUCCESS,
            instance_name="Test",
            instance_slug="test",
            instance_color="#10B981",
            title="Backup successful",
            message="Created test.tar.gz",
            filename="test.tar.gz",
            file_size=2048,
            trigger="manual",
        )
        backend.send(self.config, payload)

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(
            req.full_url,
            "http://ha.local:8123/api/services/persistent_notification/create",
        )
        self.assertEqual(req.get_header("Authorization"), "Bearer eyJtoken")
        body = json.loads(req.data)
        self.assertIn("Backup successful", body["title"])
        self.assertEqual(body["notification_id"], "flowhistory_test_backup_success")

    @patch("backup.services.notifications.homeassistant.urlopen")
    @patch.dict(
        os.environ,
        {
            "FLOWHISTORY_TEST_HOMEASSISTANT_URL": "http://ha.local:8123",
            "FLOWHISTORY_TEST_HOMEASSISTANT_TOKEN": "eyJtoken",
        },
    )
    def test_send_handles_failure(self, mock_urlopen):
        from urllib.error import URLError

        from backup.services.notifications.homeassistant import HomeAssistantBackend

        mock_urlopen.side_effect = URLError("Connection refused")
        backend = HomeAssistantBackend()
        payload = NotificationPayload(
            event=NotifyEvent.BACKUP_FAILED,
            instance_name="Test",
            instance_slug="test",
            instance_color="#EF4444",
            title="Failed",
            message="Failed",
        )
        backend.send(self.config, payload)


class BackupNotificationIntegrationTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            flows_path=str(self.flows_file),
            notify_enabled=True,
        )

    @patch("backup.services.notification_service.notify")
    def test_successful_backup_triggers_notification(self, mock_notify):
        record = create_backup(config=self.config, trigger="manual")
        self.assertEqual(record.status, "success")
        mock_notify.assert_called_once()
        _, payload = mock_notify.call_args[0]
        self.assertEqual(payload.event, NotifyEvent.BACKUP_SUCCESS)
        self.assertEqual(payload.filename, record.filename)

    @patch("backup.services.notification_service.notify")
    def test_failed_backup_triggers_notification(self, mock_notify):
        self.config.flows_path = "/nonexistent/flows.json"
        self.config.save()
        record = create_backup(config=self.config, trigger="manual")
        self.assertEqual(record.status, "failed")
        mock_notify.assert_called_once()
        _, payload = mock_notify.call_args[0]
        self.assertEqual(payload.event, NotifyEvent.BACKUP_FAILED)
        self.assertIsNotNone(payload.error)

    @patch("backup.services.notification_service.notify")
    def test_notification_failure_does_not_break_backup(self, mock_notify):
        mock_notify.side_effect = Exception("Notification system down")
        record = create_backup(config=self.config, trigger="manual")
        # Backup should still succeed despite notification failure
        self.assertEqual(record.status, "success")


class RestoreNotificationIntegrationTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            flows_path=str(self.flows_file),
            notify_enabled=True,
        )
        self.backup_record = create_backup(config=self.config, trigger="manual")

    @patch("backup.services.notification_service.notify")
    @patch("backup.services.restore_service.restart_container")
    def test_successful_restore_triggers_notification(self, mock_restart, mock_notify):
        result = restore_backup(self.backup_record.pk)
        self.assertEqual(result.status, "success")
        mock_notify.assert_called()
        _, payload = mock_notify.call_args[0]
        self.assertEqual(payload.event, NotifyEvent.RESTORE_SUCCESS)

    @patch("backup.services.notification_service.notify")
    def test_failed_restore_triggers_notification(self, mock_notify):
        from pathlib import Path

        # Corrupt the archive
        Path(self.backup_record.file_path).write_text("not a tar")
        result = restore_backup(self.backup_record.pk)
        self.assertEqual(result.status, "failed")
        mock_notify.assert_called()
        _, payload = mock_notify.call_args[0]
        self.assertEqual(payload.event, NotifyEvent.RESTORE_FAILED)


class RetentionNotificationIntegrationTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            flows_path=str(self.flows_file),
            max_backups=1,
            notify_enabled=True,
        )

    @patch("backup.services.notification_service.notify")
    def test_retention_cleanup_triggers_notification(self, mock_notify):
        from backup.services.retention_service import apply_retention

        # Create 2 backups with different checksums
        self.flows_file.write_text(
            json.dumps(SAMPLE_FLOWS + [{"id": "extra1", "type": "inject"}])
        )
        create_backup(config=self.config, trigger="manual")
        self.flows_file.write_text(
            json.dumps(SAMPLE_FLOWS + [{"id": "extra2", "type": "debug"}])
        )
        create_backup(config=self.config, trigger="manual")

        mock_notify.reset_mock()
        result = apply_retention(self.config)

        if result["deleted_by_count"] + result["deleted_by_age"] > 0:
            mock_notify.assert_called_once()
            _, payload = mock_notify.call_args[0]
            self.assertEqual(payload.event, NotifyEvent.RETENTION_CLEANUP)

    @patch("backup.services.notification_service.notify")
    def test_no_notification_when_nothing_deleted(self, mock_notify):
        from backup.services.retention_service import apply_retention

        create_backup(config=self.config, trigger="manual")
        mock_notify.reset_mock()
        result = apply_retention(self.config)
        if result["deleted_by_count"] == 0 and result["deleted_by_age"] == 0:
            mock_notify.assert_not_called()


@override_settings(REQUIRE_AUTH=False)
class ApiTestNotificationTest(TestCase):
    def setUp(self):
        self.config = NodeRedConfig.objects.create(name="Test", env_prefix="TEST")

    @patch.dict(os.environ, {}, clear=True)
    def test_no_backends_returns_400(self):
        resp = self.client.post(f"/api/instance/{self.config.slug}/notifications/test/")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("No notification backends", resp.json()["message"])

    @patch("backup.services.notification_service._get_backends")
    def test_successful_test_notification(self, mock_get):
        mock_backend = MagicMock()
        mock_backend.is_configured.return_value = True
        mock_backend.name.return_value = "Discord"
        mock_get.return_value = [mock_backend]

        resp = self.client.post(f"/api/instance/{self.config.slug}/notifications/test/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["backends"], ["Discord"])
        mock_backend.send.assert_called_once()
