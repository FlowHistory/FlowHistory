import json
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase

from backup.models import BackupRecord, NodeRedConfig
from backup.services.backup_service import create_backup
from backup.tests.helpers import SAMPLE_FLOWS, TempBackupDirMixin


class CheckIntegrityTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            name="Integrity",
            flows_path=str(self.flows_file),
        )

    def test_no_orphans(self):
        create_backup(self.config, trigger="manual")
        out = StringIO()
        call_command("checkintegrity", stdout=out)
        self.assertIn("all backup files present", out.getvalue())

    def test_orphans_found_warn_only(self):
        rec = create_backup(self.config, trigger="manual")
        # Delete the file but keep the DB record
        Path(rec.file_path).unlink()

        out, err = StringIO(), StringIO()
        call_command("checkintegrity", stdout=out, stderr=err)
        self.assertIn("Orphaned record", err.getvalue())
        self.assertIn("Run with --delete", err.getvalue())
        # Record still exists
        self.assertTrue(BackupRecord.objects.filter(pk=rec.pk).exists())

    def test_orphans_deleted_with_flag(self):
        rec = create_backup(self.config, trigger="manual")
        Path(rec.file_path).unlink()

        err = StringIO()
        call_command("checkintegrity", "--delete", stdout=StringIO(), stderr=err)
        self.assertIn("Deleted", err.getvalue())
        self.assertFalse(BackupRecord.objects.filter(pk=rec.pk).exists())

    def test_existing_files_not_flagged(self):
        rec = create_backup(self.config, trigger="manual")
        self.assertTrue(Path(rec.file_path).is_file())

        out = StringIO()
        call_command("checkintegrity", stdout=out)
        self.assertIn("all backup files present", out.getvalue())


class ScheduledBackupTest(TestCase):
    def setUp(self):
        self.config = NodeRedConfig.objects.create(
            name="Scheduled",
            schedule_enabled=True,
        )

    @patch("backup.management.commands.runapscheduler.create_backup")
    def test_successful_backup(self, mock_backup):
        from backup.management.commands.runapscheduler import _scheduled_backup

        mock_backup.return_value = MagicMock(status="success", filename="test.tar.gz")
        _scheduled_backup(self.config.pk)
        mock_backup.assert_called_once()
        self.assertEqual(mock_backup.call_args.kwargs["trigger"], "scheduled")

    @patch("backup.management.commands.runapscheduler.create_backup")
    def test_skips_when_schedule_disabled(self, mock_backup):
        from backup.management.commands.runapscheduler import _scheduled_backup

        self.config.schedule_enabled = False
        self.config.save()
        _scheduled_backup(self.config.pk)
        mock_backup.assert_not_called()

    @patch("backup.management.commands.runapscheduler.create_backup")
    def test_no_changes_returns_none(self, mock_backup):
        from backup.management.commands.runapscheduler import _scheduled_backup

        mock_backup.return_value = None
        _scheduled_backup(self.config.pk)  # Should not raise

    @patch("backup.management.commands.runapscheduler.create_backup")
    def test_deleted_config_handled(self, mock_backup):
        from backup.management.commands.runapscheduler import _scheduled_backup

        _scheduled_backup(9999)  # Non-existent config ID
        mock_backup.assert_not_called()

    @patch("backup.management.commands.runapscheduler.create_backup")
    def test_exception_does_not_crash(self, mock_backup):
        from backup.management.commands.runapscheduler import _scheduled_backup

        mock_backup.side_effect = RuntimeError("disk full")
        _scheduled_backup(self.config.pk)  # Should not raise


class ScheduledRetentionTest(TestCase):
    def setUp(self):
        self.config = NodeRedConfig.objects.create(name="Retention")

    @patch("backup.management.commands.runapscheduler.apply_retention")
    def test_successful_retention(self, mock_retention):
        from backup.management.commands.runapscheduler import _scheduled_retention

        mock_retention.return_value = {"deleted_by_count": 2, "deleted_by_age": 1}
        _scheduled_retention(self.config.pk)
        mock_retention.assert_called_once()

    @patch("backup.management.commands.runapscheduler.apply_retention")
    def test_deleted_config_handled(self, mock_retention):
        from backup.management.commands.runapscheduler import _scheduled_retention

        _scheduled_retention(9999)
        mock_retention.assert_not_called()

    @patch("backup.management.commands.runapscheduler.apply_retention")
    def test_exception_does_not_crash(self, mock_retention):
        from backup.management.commands.runapscheduler import _scheduled_retention

        mock_retention.side_effect = RuntimeError("db locked")
        _scheduled_retention(self.config.pk)  # Should not raise
