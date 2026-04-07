import hashlib
import json
import tarfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings

from backup.models import BackupRecord, NodeRedConfig
from backup.services.backup_service import create_backup
from backup.tests.helpers import SAMPLE_FLOWS, TempBackupDirMixin


class BackupServiceTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
        )

    def test_create_backup_success(self):
        record = create_backup(config=self.config, trigger="manual")
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "success")
        self.assertEqual(record.trigger, "manual")
        self.assertTrue(record.filename.startswith("flowhistory_"))
        self.assertTrue(record.filename.endswith(".tar.gz"))
        self.assertGreater(record.file_size, 0)
        self.assertEqual(len(record.checksum), 64)
        # Archive exists on disk
        self.assertTrue(Path(record.file_path).is_file())

    def test_archive_contains_flows_json(self):
        record = create_backup(config=self.config, trigger="manual")
        with tarfile.open(record.file_path, "r:gz") as tar:
            names = tar.getnames()
            self.assertIn("flows.json", names)
            f = tar.extractfile("flows.json")
            data = json.loads(f.read())
            self.assertEqual(data, SAMPLE_FLOWS)

    def test_tab_summary_populated(self):
        record = create_backup(config=self.config, trigger="manual")
        self.assertEqual(record.tab_summary, ["API Endpoints", "Home Automation"])

    def test_checksum_matches_flows_content(self):
        record = create_backup(config=self.config, trigger="manual")
        expected = hashlib.sha256(self.flows_file.read_bytes()).hexdigest()
        self.assertEqual(record.checksum, expected)

    def test_config_updated_on_success(self):
        create_backup(config=self.config, trigger="manual")
        self.config.refresh_from_db()
        self.assertIsNotNone(self.config.last_successful_backup)
        self.assertEqual(self.config.last_backup_error, "")

    def test_missing_directory_returns_volume_error(self):
        self.config.flows_path = "/nonexistent/flows.json"
        self.config.save()
        record = create_backup(config=self.config, trigger="manual")
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "failed")
        self.assertIn("does not exist", record.error_message)
        self.assertIn("volume mounted", record.error_message)

    def test_missing_file_in_existing_dir_returns_not_found(self):
        self.config.flows_path = str(self.backup_dir / "missing_flows.json")
        self.config.save()
        record = create_backup(config=self.config, trigger="manual")
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "failed")
        self.assertIn("not found", record.error_message)

    def test_dedup_skips_for_scheduled(self):
        create_backup(config=self.config, trigger="manual")
        result = create_backup(config=self.config, trigger="scheduled")
        self.assertIsNone(result)

    def test_always_backup_bypasses_dedup_for_scheduled(self):
        self.config.always_backup = True
        self.config.save()
        create_backup(config=self.config, trigger="manual")
        result = create_backup(config=self.config, trigger="scheduled")
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "success")

    def test_always_backup_does_not_bypass_dedup_for_file_change(self):
        self.config.always_backup = True
        self.config.save()
        create_backup(config=self.config, trigger="manual")
        result = create_backup(config=self.config, trigger="file_change")
        self.assertIsNone(result)

    def test_always_backup_defaults_to_false(self):
        config = NodeRedConfig.objects.create(pk=99)
        self.assertFalse(config.always_backup)

    def test_dedup_does_not_skip_manual(self):
        create_backup(config=self.config, trigger="manual")
        record = create_backup(config=self.config, trigger="manual")
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "success")

    def test_changes_summary_first_backup(self):
        record = create_backup(config=self.config, trigger="manual")
        # No previous backup, so changes_summary is empty
        self.assertEqual(record.changes_summary, {})

    def test_changes_summary_detects_tab_added(self):
        create_backup(config=self.config, trigger="manual")
        # Add a new tab
        new_flows = SAMPLE_FLOWS + [{"id": "tab3", "type": "tab", "label": "New Tab"}]
        self.flows_file.write_text(json.dumps(new_flows))
        record = create_backup(config=self.config, trigger="manual")
        self.assertIn("New Tab", record.changes_summary.get("tabs_added", []))

    def test_changes_summary_detects_node_modification(self):
        create_backup(config=self.config, trigger="manual")
        # Modify an existing node's name (n1 in SAMPLE_FLOWS)
        modified_flows = []
        for node in SAMPLE_FLOWS:
            if node.get("id") == "n1":
                node = {**node, "name": "Renamed Trigger"}
            modified_flows.append(node)
        self.flows_file.write_text(json.dumps(modified_flows))
        record = create_backup(config=self.config, trigger="manual")
        tabs_mod = record.changes_summary.get("tabs_modified", [])
        self.assertTrue(len(tabs_mod) > 0)
        home_tab = [t for t in tabs_mod if t["label"] == "Home Automation"]
        self.assertEqual(len(home_tab), 1)
        self.assertTrue(len(home_tab[0]["nodes_modified"]) > 0)

    def test_includes_credentials_when_present(self):
        cred_file = self.backup_dir / "flows_cred.json"
        cred_file.write_text('{"encrypted": true}')
        self.config.backup_credentials = True
        self.config.save()
        record = create_backup(config=self.config, trigger="manual")
        self.assertTrue(record.includes_credentials)
        with tarfile.open(record.file_path, "r:gz") as tar:
            self.assertIn("flows_cred.json", tar.getnames())
        cred_file.unlink()

    def test_excludes_credentials_when_disabled(self):
        cred_file = self.backup_dir / "flows_cred.json"
        cred_file.write_text('{"encrypted": true}')
        self.config.backup_credentials = False
        self.config.save()
        record = create_backup(config=self.config, trigger="manual")
        self.assertFalse(record.includes_credentials)
        with tarfile.open(record.file_path, "r:gz") as tar:
            self.assertNotIn("flows_cred.json", tar.getnames())
        cred_file.unlink()


@override_settings(REQUIRE_AUTH=False)
class ApiCreateBackupTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
        )

    def test_post_creates_backup(self):
        resp = self.client.post(f"/api/instance/{self.config.slug}/backup/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("backup", data)
        self.assertIn("filename", data["backup"])

    def test_get_not_allowed(self):
        resp = self.client.get(f"/api/instance/{self.config.slug}/backup/")
        self.assertEqual(resp.status_code, 405)

    def test_missing_flows_returns_500(self):
        self.config.flows_path = "/nonexistent/flows.json"
        self.config.save()
        resp = self.client.post(f"/api/instance/{self.config.slug}/backup/")
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json()["status"], "error")
