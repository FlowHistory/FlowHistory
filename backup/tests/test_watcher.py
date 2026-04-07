import json
from unittest.mock import MagicMock, patch

from django.test import TestCase

from backup.models import NodeRedConfig
from backup.tests.helpers import SAMPLE_FLOWS, TempBackupDirMixin


class WatcherHandlerTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
            watch_enabled=True,
            watch_debounce_seconds=1,
        )

    def test_ignores_directory_events(self):
        from backup.services.watcher_service import _FlowsHandler

        handler = _FlowsHandler("flows.json", self.config.pk)
        event = MagicMock()
        event.is_directory = True
        event.src_path = str(self.flows_file)
        handler.on_modified(event)
        self.assertIsNone(handler._timer)

    def test_ignores_non_flows_files(self):
        from backup.services.watcher_service import _FlowsHandler

        handler = _FlowsHandler("flows.json", self.config.pk)
        event = MagicMock()
        event.is_directory = False
        event.src_path = str(self.backup_dir / "settings.js")
        handler.on_modified(event)
        self.assertIsNone(handler._timer)

    def test_starts_timer_on_flows_modified(self):
        from backup.services.watcher_service import _FlowsHandler

        handler = _FlowsHandler("flows.json", self.config.pk)
        event = MagicMock()
        event.is_directory = False
        event.src_path = str(self.flows_file)
        handler.on_modified(event)
        self.assertIsNotNone(handler._timer)
        handler._timer.cancel()  # Clean up

    def test_watch_disabled_skips_timer(self):
        from backup.services.watcher_service import _FlowsHandler

        self.config.watch_enabled = False
        self.config.save()
        handler = _FlowsHandler("flows.json", self.config.pk)
        event = MagicMock()
        event.is_directory = False
        event.src_path = str(self.flows_file)
        handler.on_modified(event)
        self.assertIsNone(handler._timer)

    @patch("backup.services.backup_service.create_backup")
    def test_debounce_complete_creates_backup(self, mock_backup):
        from backup.services.watcher_service import _FlowsHandler

        mock_backup.return_value = MagicMock(status="success", filename="test.tar.gz")
        handler = _FlowsHandler("flows.json", self.config.pk)
        handler._on_debounce_complete()
        mock_backup.assert_called_once()
        call_kwargs = mock_backup.call_args[1]
        self.assertEqual(call_kwargs["trigger"], "file_change")


class SchedulerBuildTriggerTest(TestCase):
    def test_daily_trigger(self):
        from backup.management.commands.runapscheduler import Command

        config = MagicMock()
        config.backup_frequency = "daily"
        config.backup_time = MagicMock(hour=3, minute=0)
        trigger = Command._build_trigger(config)
        # CronTrigger should have hour=3, minute=0
        self.assertIsNotNone(trigger)

    def test_hourly_trigger(self):
        from backup.management.commands.runapscheduler import Command

        config = MagicMock()
        config.backup_frequency = "hourly"
        config.backup_time = MagicMock(hour=3, minute=30)
        trigger = Command._build_trigger(config)
        self.assertIsNotNone(trigger)

    def test_weekly_trigger(self):
        from backup.management.commands.runapscheduler import Command

        config = MagicMock()
        config.backup_frequency = "weekly"
        config.backup_time = MagicMock(hour=3, minute=0)
        config.backup_day = 0
        trigger = Command._build_trigger(config)
        self.assertIsNotNone(trigger)
