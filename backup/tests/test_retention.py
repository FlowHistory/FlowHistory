import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from backup.models import BackupRecord, NodeRedConfig
from backup.services.backup_service import create_backup
from backup.tests.helpers import SAMPLE_FLOWS, TempBackupDirMixin


class RetentionServiceTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
            max_backups=3,
            max_age_days=7,
        )

    def _create_backups(self, count, **kwargs):
        """Create multiple manual backups with unique content.

        Mocks apply_retention during creation so retention doesn't run
        prematurely (backup_service calls it after each success).
        """
        records = []
        with patch("backup.services.retention_service.apply_retention"):
            for i in range(count):
                flows = SAMPLE_FLOWS + [{"id": f"extra_{i}", "type": "inject", "z": "tab1"}]
                self.flows_file.write_text(json.dumps(flows))
                record = create_backup(config=self.config, trigger="manual")
                if record and kwargs.get("age_days"):
                    # Backdate the record
                    record.created_at = record.created_at - timedelta(days=kwargs["age_days"])
                    record.save(update_fields=["created_at"])
                records.append(record)
        return records

    def test_delete_by_count(self):
        from backup.services.retention_service import apply_retention

        self._create_backups(5)
        self.assertEqual(BackupRecord.objects.filter(status="success").count(), 5)
        result = apply_retention(self.config)
        self.assertEqual(BackupRecord.objects.filter(status="success").count(), 3)
        self.assertEqual(result["deleted_by_count"], 2)

    def test_delete_by_age(self):
        from backup.services.retention_service import apply_retention

        records = self._create_backups(2)
        # Backdate both records to 10 days ago
        for r in records:
            r.created_at = r.created_at - timedelta(days=10)
            r.save(update_fields=["created_at"])
        result = apply_retention(self.config)
        self.assertEqual(result["deleted_by_age"], 2)
        self.assertEqual(BackupRecord.objects.filter(status="success").count(), 0)

    def test_protects_recent_pre_restore(self):
        from backup.services.retention_service import apply_retention

        # Create a pre_restore backup
        record = create_backup(config=self.config, trigger="pre_restore")
        # Create enough to exceed max_backups
        self._create_backups(4)
        result = apply_retention(self.config)
        # pre_restore should still exist
        self.assertTrue(
            BackupRecord.objects.filter(pk=record.pk).exists()
        )

    def test_disk_file_deleted(self):
        from backup.services.retention_service import apply_retention

        records = self._create_backups(5)
        oldest_path = Path(records[0].file_path)
        self.assertTrue(oldest_path.is_file())
        apply_retention(self.config)
        self.assertFalse(oldest_path.is_file())

    def test_no_deletions_within_limits(self):
        from backup.services.retention_service import apply_retention

        self._create_backups(2)
        result = apply_retention(self.config)
        self.assertEqual(result["deleted_by_count"], 0)
        self.assertEqual(result["deleted_by_age"], 0)

    def test_pinned_protected_from_count_deletion(self):
        from backup.services.retention_service import apply_retention

        records = self._create_backups(5)
        # Pin the oldest backup
        records[0].is_pinned = True
        records[0].save(update_fields=["is_pinned"])
        result = apply_retention(self.config)
        # Oldest should survive because it's pinned
        self.assertTrue(BackupRecord.objects.filter(pk=records[0].pk).exists())
        # Only 1 deleted (not 2) because pinned one is protected
        self.assertEqual(result["deleted_by_count"], 1)

    def test_pinned_protected_from_age_deletion(self):
        from backup.services.retention_service import apply_retention

        records = self._create_backups(2)
        # Backdate and pin one
        for r in records:
            r.created_at = r.created_at - timedelta(days=10)
            r.save(update_fields=["created_at"])
        records[0].is_pinned = True
        records[0].save(update_fields=["is_pinned"])
        result = apply_retention(self.config)
        self.assertEqual(result["deleted_by_age"], 1)
        self.assertTrue(BackupRecord.objects.filter(pk=records[0].pk).exists())
