import hashlib
import json
import tarfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings

from backup.models import BackupRecord, NodeRedConfig, RestoreRecord
from backup.services.backup_service import create_backup
from backup.services.restore_service import restore_backup
from backup.tests.helpers import SAMPLE_FLOWS, TempBackupDirMixin


class RestoreServiceTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
        )
        # Create a backup to restore from
        self.backup_record = create_backup(config=self.config, trigger="manual")

    def test_restore_success(self):
        # Modify flows.json so restore actually overwrites
        self.flows_file.write_text("[]")
        result = restore_backup(self.backup_record.pk)
        self.assertEqual(result.status, "success")
        self.assertIsInstance(result, RestoreRecord)
        # Verify flows.json was restored
        restored = json.loads(self.flows_file.read_text())
        self.assertEqual(restored, SAMPLE_FLOWS)

    def test_restore_creates_safety_backup(self):
        restore_backup(self.backup_record.pk)
        safety = BackupRecord.objects.filter(config=self.config, trigger="pre_restore")
        self.assertTrue(safety.exists())

    def test_restore_record_tracks_safety_backup(self):
        result = restore_backup(self.backup_record.pk)
        self.assertIsNotNone(result.safety_backup)
        self.assertEqual(result.safety_backup.trigger, "pre_restore")

    def test_restore_files_list(self):
        result = restore_backup(self.backup_record.pk)
        self.assertIn("flows.json", result.files_restored)

    def test_restore_invalid_id_raises(self):
        with self.assertRaises(BackupRecord.DoesNotExist):
            restore_backup(99999)

    def test_restore_failed_backup_rejected(self):
        failed = BackupRecord.objects.create(
            config=self.config,
            filename="bad.tar.gz",
            file_path="/nonexistent/bad.tar.gz",
            status="failed",
            trigger="manual",
        )
        result = restore_backup(failed.pk)
        self.assertEqual(result.status, "failed")
        self.assertIn("Cannot restore", result.error_message)

    def test_restore_missing_archive(self):
        Path(self.backup_record.file_path).unlink()
        result = restore_backup(self.backup_record.pk)
        self.assertEqual(result.status, "failed")
        self.assertIn("not found", result.error_message)

    @patch("backup.services.restore_service.os.chown")
    def test_restore_sets_ownership(self, mock_chown):
        restore_backup(self.backup_record.pk)
        mock_chown.assert_called()
        args = mock_chown.call_args[0]
        self.assertEqual(args[1], 1000)
        self.assertEqual(args[2], 1000)

    @patch("backup.services.restore_service.restart_container")
    def test_restore_with_restart(self, mock_restart):
        mock_restart.return_value = {"success": True, "message": "Restarted"}
        self.config.restart_on_restore = True
        self.config.save()
        result = restore_backup(self.backup_record.pk)
        self.assertTrue(result.container_restarted)
        mock_restart.assert_called_once_with(self.config.nodered_container_name)

    @patch("backup.services.restore_service.restart_container")
    def test_restore_without_restart(self, mock_restart):
        self.config.restart_on_restore = False
        self.config.save()
        result = restore_backup(self.backup_record.pk)
        self.assertFalse(result.container_restarted)
        mock_restart.assert_not_called()

    @patch("backup.services.restore_service.restart_container")
    def test_restore_restart_override(self, mock_restart):
        mock_restart.return_value = {"success": True, "message": "Restarted"}
        self.config.restart_on_restore = False
        self.config.save()
        result = restore_backup(self.backup_record.pk, restart=True)
        self.assertTrue(result.container_restarted)
        mock_restart.assert_called_once()

    def test_restore_with_credentials(self):
        # Create a backup that includes credentials
        cred_file = self.backup_dir / "flows_cred.json"
        cred_file.write_text('{"encrypted": true}')
        self.config.backup_credentials = True
        self.config.save()
        backup = create_backup(config=self.config, trigger="manual")
        cred_file.unlink()  # Remove the original
        result = restore_backup(backup.pk)
        self.assertEqual(result.status, "success")
        self.assertIn("flows_cred.json", result.files_restored)
        self.assertTrue(cred_file.is_file())  # Should be restored


@override_settings(REQUIRE_AUTH=False)
class ApiRestoreBackupTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
        )
        self.backup_record = create_backup(config=self.config, trigger="manual")

    def test_post_restores_backup(self):
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/restore/{self.backup_record.pk}/"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("restore", data)
        self.assertIn("files_restored", data["restore"])

    def test_get_not_allowed(self):
        resp = self.client.get(
            f"/api/instance/{self.config.slug}/restore/{self.backup_record.pk}/"
        )
        self.assertEqual(resp.status_code, 405)

    def test_nonexistent_backup_returns_404(self):
        resp = self.client.post(f"/api/instance/{self.config.slug}/restore/99999/")
        self.assertEqual(resp.status_code, 404)

    def test_response_includes_safety_backup(self):
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/restore/{self.backup_record.pk}/"
        )
        data = resp.json()
        self.assertIn("safety_backup_id", data["restore"])


@override_settings(REQUIRE_AUTH=False)
class RemoteRestoreTest(TempBackupDirMixin, TestCase):
    """Tests for restoring backups to remote Node-RED instances."""

    def setUp(self):
        super().setUp()
        self.config = NodeRedConfig.objects.create(
            name="Remote Restore",
            source_type="remote",
            nodered_url="http://fake:1880",
            env_prefix="REMOTE",
        )
        # Create a backup archive with flows.json
        flows_bytes = json.dumps(SAMPLE_FLOWS).encode()
        backup_dir = self.config.backup_dir
        backup_dir.mkdir(parents=True, exist_ok=True)
        archive_path = backup_dir / "test_restore.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tar:
            info = tarfile.TarInfo(name="flows.json")
            info.size = len(flows_bytes)
            tar.addfile(info, BytesIO(flows_bytes))
        checksum = hashlib.sha256(flows_bytes).hexdigest()
        self.backup_record = BackupRecord.objects.create(
            config=self.config,
            filename="test_restore.tar.gz",
            file_path=str(archive_path),
            file_size=archive_path.stat().st_size,
            checksum=checksum,
            status="success",
            trigger="manual",
        )

    @patch("backup.services.remote_service.deploy_remote_flows")
    @patch("backup.services.restore_service.create_backup")
    def test_remote_restore_deploys_flows(self, mock_safety, mock_deploy):
        mock_safety.return_value = None
        result = restore_backup(self.backup_record.pk)
        self.assertEqual(result.status, "success")
        mock_deploy.assert_called_once()
        call_args = mock_deploy.call_args
        self.assertEqual(call_args[0][0], self.config)
        # Verify flows.json content was passed
        deployed = json.loads(call_args[0][1])
        self.assertEqual(deployed, SAMPLE_FLOWS)

    @patch("backup.services.remote_service.deploy_remote_flows")
    @patch("backup.services.restore_service.create_backup")
    def test_remote_restore_records_files(self, mock_safety, mock_deploy):
        mock_safety.return_value = None
        result = restore_backup(self.backup_record.pk)
        self.assertEqual(result.files_restored, ["flows.json"])

    @patch("backup.services.remote_service.deploy_remote_flows")
    @patch("backup.services.restore_service.create_backup")
    def test_remote_restore_deploy_failure(self, mock_safety, mock_deploy):
        mock_safety.return_value = None
        mock_deploy.side_effect = Exception("Connection refused")
        result = restore_backup(self.backup_record.pk)
        self.assertEqual(result.status, "failed")
        self.assertIn("Failed to deploy", result.error_message)

    @patch("backup.services.remote_service.deploy_remote_flows")
    @patch("backup.services.restore_service.create_backup")
    def test_remote_restore_no_container_restart(self, mock_safety, mock_deploy):
        """Remote restore should not attempt container restart."""
        mock_safety.return_value = None
        self.config.restart_on_restore = True
        self.config.save()
        result = restore_backup(self.backup_record.pk)
        self.assertEqual(result.status, "success")
        self.assertFalse(result.container_restarted)

    def test_remote_restore_api_endpoint(self):
        """API endpoint should accept restore for remote instances."""
        with patch("backup.views.api.restore_backup") as mock_restore:
            mock_restore.return_value = RestoreRecord(
                config=self.config,
                backup=self.backup_record,
                status="success",
                files_restored=["flows.json"],
            )
            resp = self.client.post(
                f"/api/instance/{self.config.slug}/restore/{self.backup_record.pk}/"
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "success")
