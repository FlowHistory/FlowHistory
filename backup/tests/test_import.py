import io
import json
import tarfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from backup.models import BackupRecord, NodeRedConfig
from backup.services.backup_service import create_backup
from backup.services.import_service import ImportValidationError, import_backup
from backup.services.notifications.base import NotifyEvent
from backup.tests.helpers import SAMPLE_FLOWS, TempBackupDirMixin


def create_test_archive(
    flows=None,
    include_creds=False,
    include_settings=False,
    extra_files=None,
    add_symlink=False,
    add_path_traversal=False,
):
    """Build an in-memory .tar.gz archive for testing.

    Args:
        flows: JSON-serializable data for flows.json. Defaults to SAMPLE_FLOWS.
        include_creds: Include a flows_cred.json member.
        include_settings: Include a settings.js member.
        extra_files: Dict of {name: bytes} to add as extra members.
        add_symlink: Add a symlink member.
        add_path_traversal: Add a member with path traversal.

    Returns:
        bytes of the tar.gz archive.
    """
    if flows is None:
        flows = SAMPLE_FLOWS
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        flows_bytes = json.dumps(flows).encode()
        info = tarfile.TarInfo(name="flows.json")
        info.size = len(flows_bytes)
        tar.addfile(info, io.BytesIO(flows_bytes))

        if include_creds:
            cred_bytes = b'{"_": "encrypted"}'
            info = tarfile.TarInfo(name="flows_cred.json")
            info.size = len(cred_bytes)
            tar.addfile(info, io.BytesIO(cred_bytes))

        if include_settings:
            settings_bytes = b"module.exports = {};"
            info = tarfile.TarInfo(name="settings.js")
            info.size = len(settings_bytes)
            tar.addfile(info, io.BytesIO(settings_bytes))

        if extra_files:
            for name, data in extra_files.items():
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))

        if add_symlink:
            info = tarfile.TarInfo(name="link.json")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)

        if add_path_traversal:
            data = b"malicious"
            info = tarfile.TarInfo(name="../etc/passwd")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    return buf.getvalue()


def make_upload(archive_bytes, filename="backup.tar.gz"):
    """Wrap archive bytes as a SimpleUploadedFile."""
    return SimpleUploadedFile(filename, archive_bytes, content_type="application/gzip")


class ImportServiceTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.config = NodeRedConfig.objects.create(pk=1, name="Test")

    def test_valid_archive_import(self):
        archive = create_test_archive()
        record, dup_warning = import_backup(self.config, make_upload(archive))
        self.assertEqual(record.trigger, "import")
        self.assertEqual(record.status, "success")
        self.assertIsNone(dup_warning)

    def test_archive_with_credentials_and_settings(self):
        archive = create_test_archive(include_creds=True, include_settings=True)
        record, _ = import_backup(self.config, make_upload(archive))
        self.assertTrue(record.includes_credentials)
        self.assertTrue(record.includes_settings)

    def test_archive_without_credentials_and_settings(self):
        archive = create_test_archive()
        record, _ = import_backup(self.config, make_upload(archive))
        self.assertFalse(record.includes_credentials)
        self.assertFalse(record.includes_settings)

    def test_tab_summary_populated(self):
        archive = create_test_archive()
        record, _ = import_backup(self.config, make_upload(archive))
        self.assertIn("Home Automation", record.tab_summary)
        self.assertIn("API Endpoints", record.tab_summary)

    def test_changes_summary_computed(self):
        # Create a first backup so the import has something to diff against
        flows_file = self.backup_dir / "flows.json"
        flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config.flows_path = str(flows_file)
        self.config.save()
        create_backup(self.config, trigger="manual")

        # Import with a modified flows — add a new tab
        modified = SAMPLE_FLOWS + [{"id": "tab3", "type": "tab", "label": "New Tab"}]
        archive = create_test_archive(flows=modified)
        record, _ = import_backup(self.config, make_upload(archive))
        self.assertIn("tabs_added", record.changes_summary)

    def test_checksum_computed(self):
        archive = create_test_archive()
        record, _ = import_backup(self.config, make_upload(archive))
        self.assertTrue(len(record.checksum) == 64)
        self.assertNotEqual(record.checksum, "")

    def test_label_and_notes_saved(self):
        archive = create_test_archive()
        record, _ = import_backup(
            self.config, make_upload(archive), label="Migrated", notes="From server-2"
        )
        self.assertEqual(record.label, "Migrated")
        self.assertEqual(record.notes, "From server-2")

    def test_duplicate_checksum_warns_but_imports(self):
        archive = create_test_archive()
        record1, _ = import_backup(self.config, make_upload(archive))
        record2, dup_warning = import_backup(self.config, make_upload(archive))
        self.assertIsNotNone(dup_warning)
        self.assertIn("Checksum matches", dup_warning)
        self.assertNotEqual(record1.pk, record2.pk)

    def test_reject_label_too_long(self):
        archive = create_test_archive()
        with self.assertRaises(ImportValidationError) as ctx:
            import_backup(self.config, make_upload(archive), label="x" * 201)
        self.assertIn("200 characters", str(ctx.exception))

    def test_reject_non_tar_gz(self):
        upload = SimpleUploadedFile(
            "backup.txt", b"not a tar", content_type="text/plain"
        )
        with self.assertRaises(ImportValidationError) as ctx:
            import_backup(self.config, upload)
        self.assertIn(".tar.gz", str(ctx.exception))

    def test_reject_missing_flows_json(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            data = b"some data"
            info = tarfile.TarInfo(name="settings.js")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        upload = make_upload(buf.getvalue())
        with self.assertRaises(ImportValidationError) as ctx:
            import_backup(self.config, upload)
        self.assertIn("flows.json", str(ctx.exception))

    def test_reject_unexpected_files(self):
        archive = create_test_archive(extra_files={"malicious.sh": b"rm -rf /"})
        with self.assertRaises(ImportValidationError) as ctx:
            import_backup(self.config, make_upload(archive))
        self.assertIn("unexpected files", str(ctx.exception))

    def test_reject_symlinks(self):
        # Symlink with a whitelisted name so it passes the unexpected files check
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            flows_bytes = json.dumps(SAMPLE_FLOWS).encode()
            info = tarfile.TarInfo(name="flows.json")
            info.size = len(flows_bytes)
            tar.addfile(info, io.BytesIO(flows_bytes))
            # Add a symlink with a whitelisted name
            link_info = tarfile.TarInfo(name="flows_cred.json")
            link_info.type = tarfile.SYMTYPE
            link_info.linkname = "/etc/passwd"
            tar.addfile(link_info)
        with self.assertRaises(ImportValidationError) as ctx:
            import_backup(self.config, make_upload(buf.getvalue()))
        self.assertIn("symbolic or hard links", str(ctx.exception))

    def test_reject_path_traversal(self):
        archive = create_test_archive(add_path_traversal=True)
        with self.assertRaises(ImportValidationError) as ctx:
            import_backup(self.config, make_upload(archive))
        # Could be caught by path traversal or unexpected files check
        self.assertTrue(
            "path traversal" in str(ctx.exception).lower()
            or "unexpected files" in str(ctx.exception).lower()
        )

    def test_reject_invalid_flows_json(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            data = b"not valid json"
            info = tarfile.TarInfo(name="flows.json")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        upload = make_upload(buf.getvalue())
        with self.assertRaises(ImportValidationError) as ctx:
            import_backup(self.config, upload)
        self.assertIn("not valid JSON", str(ctx.exception))

    def test_reject_non_array_flows_json(self):
        archive = create_test_archive(flows={"not": "an array"})
        with self.assertRaises(ImportValidationError) as ctx:
            import_backup(self.config, make_upload(archive))
        self.assertIn("JSON array", str(ctx.exception))

    @override_settings(IMPORT_MAX_SIZE=100)
    def test_reject_oversized_archive(self):
        archive = create_test_archive()
        with self.assertRaises(ImportValidationError) as ctx:
            import_backup(self.config, make_upload(archive))
        self.assertIn("exceeds maximum size", str(ctx.exception))

    def test_archive_stored_in_instance_backup_dir(self):
        archive = create_test_archive()
        record, _ = import_backup(self.config, make_upload(archive))
        self.assertTrue(record.file_path.startswith(str(self.config.backup_dir)))

    def test_original_filename_not_preserved(self):
        archive = create_test_archive()
        record, _ = import_backup(
            self.config, make_upload(archive, filename="my-custom-backup.tar.gz")
        )
        self.assertTrue(record.filename.startswith("flowhistory_"))


@override_settings(REQUIRE_AUTH=False)
class ApiImportBackupTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.config = NodeRedConfig.objects.create(pk=1, name="Test")
        self.url = f"/api/instance/{self.config.slug}/import/"

    def test_post_imports_backup(self):
        archive = create_test_archive()
        resp = self.client.post(
            self.url,
            data={"archive": make_upload(archive)},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["backup"]["trigger"], "import")

    def test_post_with_label_and_notes(self):
        archive = create_test_archive()
        resp = self.client.post(
            self.url,
            data={
                "archive": make_upload(archive),
                "label": "Test label",
                "notes": "Test notes",
            },
        )
        self.assertEqual(resp.status_code, 200)
        record = BackupRecord.objects.get(pk=resp.json()["backup"]["id"])
        self.assertEqual(record.label, "Test label")
        self.assertEqual(record.notes, "Test notes")

    def test_get_not_allowed(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_missing_file_returns_400(self):
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("No archive", resp.json()["message"])

    def test_invalid_archive_returns_400(self):
        upload = SimpleUploadedFile(
            "bad.tar.gz", b"not a tar", content_type="application/gzip"
        )
        resp = self.client.post(self.url, data={"archive": upload})
        self.assertEqual(resp.status_code, 400)

    @override_settings(IMPORT_MAX_SIZE=10)
    def test_oversized_archive_returns_413(self):
        archive = create_test_archive()
        resp = self.client.post(
            self.url,
            data={"archive": make_upload(archive)},
        )
        self.assertEqual(resp.status_code, 413)


class ImportNotificationIntegrationTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.config = NodeRedConfig.objects.create(
            pk=1, name="Test", notify_enabled=True
        )

    @patch("backup.services.notification_service.notify")
    def test_successful_import_triggers_notification(self, mock_notify):
        archive = create_test_archive()
        record, _ = import_backup(self.config, make_upload(archive))
        self.assertEqual(record.status, "success")
        mock_notify.assert_called_once()
        _, payload = mock_notify.call_args[0]
        self.assertEqual(payload.event, NotifyEvent.IMPORT_SUCCESS)
        self.assertEqual(payload.filename, record.filename)

    @patch("backup.services.notification_service.notify")
    def test_notification_failure_does_not_break_import(self, mock_notify):
        mock_notify.side_effect = Exception("Notification system down")
        archive = create_test_archive()
        record, _ = import_backup(self.config, make_upload(archive))
        self.assertEqual(record.status, "success")

    @patch("backup.services.retention_service.apply_retention")
    def test_import_triggers_retention(self, mock_retention):
        archive = create_test_archive()
        import_backup(self.config, make_upload(archive))
        mock_retention.assert_called_once_with(self.config)

    @patch("backup.services.retention_service.apply_retention")
    def test_retention_failure_does_not_break_import(self, mock_retention):
        mock_retention.side_effect = Exception("Retention failed")
        archive = create_test_archive()
        record, _ = import_backup(self.config, make_upload(archive))
        self.assertEqual(record.status, "success")
