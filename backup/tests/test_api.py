import json
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings

from backup.models import BackupRecord, NodeRedConfig
from backup.services.backup_service import create_backup
from backup.tests.helpers import SAMPLE_FLOWS, TempBackupDirMixin


@override_settings(REQUIRE_AUTH=False)
class ApiClearErrorTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
            last_backup_error="flows.json not found at /nodered-data/flows.json",
        )

    def test_clear_error(self):
        resp = self.client.post(f"/api/instance/{self.config.slug}/clear-error/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "success")
        self.config.refresh_from_db()
        self.assertEqual(self.config.last_backup_error, "")

    def test_clear_already_empty_error(self):
        self.config.last_backup_error = ""
        self.config.save()
        resp = self.client.post(f"/api/instance/{self.config.slug}/clear-error/")
        self.assertEqual(resp.status_code, 200)
        self.config.refresh_from_db()
        self.assertEqual(self.config.last_backup_error, "")

    def test_get_not_allowed(self):
        resp = self.client.get(f"/api/instance/{self.config.slug}/clear-error/")
        self.assertEqual(resp.status_code, 405)


@override_settings(REQUIRE_AUTH=False)
class ApiSetLabelTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
        )
        self.backup_record = create_backup(config=self.config, trigger="manual")

    def test_set_label(self):
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/label/",
            data=json.dumps({"label": "Before refactor"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["backup"]["label"], "Before refactor")
        self.backup_record.refresh_from_db()
        self.assertEqual(self.backup_record.label, "Before refactor")

    def test_clear_label(self):
        self.backup_record.label = "old label"
        self.backup_record.save()
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/label/",
            data=json.dumps({"label": ""}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.backup_record.refresh_from_db()
        self.assertEqual(self.backup_record.label, "")

    def test_missing_label_field(self):
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/label/",
            data=json.dumps({"note": "wrong field"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_label_too_long(self):
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/label/",
            data=json.dumps({"label": "x" * 201}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_not_found(self):
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/backup/99999/label/",
            data=json.dumps({"label": "test"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)


@override_settings(REQUIRE_AUTH=False)
class ApiSetNotesTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
        )
        self.backup_record = create_backup(config=self.config, trigger="manual")

    def test_set_notes(self):
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/notes/",
            data=json.dumps({"notes": "Rewired MQTT pipeline to batch writes."}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["backup"]["notes"], "Rewired MQTT pipeline to batch writes.")
        self.backup_record.refresh_from_db()
        self.assertEqual(self.backup_record.notes, "Rewired MQTT pipeline to batch writes.")

    def test_clear_notes(self):
        self.backup_record.notes = "old notes"
        self.backup_record.save()
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/notes/",
            data=json.dumps({"notes": ""}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.backup_record.refresh_from_db()
        self.assertEqual(self.backup_record.notes, "")

    def test_missing_notes_field(self):
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/notes/",
            data=json.dumps({"label": "wrong field"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_notes_not_a_string(self):
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/notes/",
            data=json.dumps({"notes": 123}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_not_found(self):
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/backup/99999/notes/",
            data=json.dumps({"notes": "test"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_multiline_notes(self):
        multiline = "Line one\nLine two\nLine three"
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/notes/",
            data=json.dumps({"notes": multiline}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.backup_record.refresh_from_db()
        self.assertEqual(self.backup_record.notes, multiline)

    def test_get_method_not_allowed(self):
        resp = self.client.get(f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/notes/")
        self.assertEqual(resp.status_code, 405)


@override_settings(REQUIRE_AUTH=False)
class ApiTogglePinTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
        )
        self.backup_record = create_backup(config=self.config, trigger="manual")

    def test_pin_backup(self):
        resp = self.client.post(f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/pin/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertTrue(data["backup"]["is_pinned"])
        self.backup_record.refresh_from_db()
        self.assertTrue(self.backup_record.is_pinned)

    def test_unpin_backup(self):
        self.backup_record.is_pinned = True
        self.backup_record.save()
        resp = self.client.post(f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/pin/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["backup"]["is_pinned"])
        self.backup_record.refresh_from_db()
        self.assertFalse(self.backup_record.is_pinned)

    def test_toggle_twice(self):
        self.client.post(f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/pin/")
        self.client.post(f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/pin/")
        self.backup_record.refresh_from_db()
        self.assertFalse(self.backup_record.is_pinned)

    def test_not_found(self):
        resp = self.client.post(f"/api/instance/{self.config.slug}/backup/99999/pin/")
        self.assertEqual(resp.status_code, 404)

    def test_get_method_not_allowed(self):
        resp = self.client.get(f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/pin/")
        self.assertEqual(resp.status_code, 405)


@override_settings(REQUIRE_AUTH=False)
class BackupDeleteTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
        )
        self.backup_record = create_backup(config=self.config, trigger="manual")

    def test_delete_removes_record_and_file(self):
        archive_path = Path(self.backup_record.file_path)
        self.assertTrue(archive_path.is_file())
        pk = self.backup_record.pk
        resp = self.client.post(f"/instance/{self.config.slug}/backup/{pk}/delete/")
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(BackupRecord.objects.filter(pk=pk).exists())
        self.assertFalse(archive_path.is_file())

    def test_delete_missing_file_still_succeeds(self):
        Path(self.backup_record.file_path).unlink()
        pk = self.backup_record.pk
        resp = self.client.post(f"/instance/{self.config.slug}/backup/{pk}/delete/")
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(BackupRecord.objects.filter(pk=pk).exists())

    def test_not_found(self):
        resp = self.client.post(f"/instance/{self.config.slug}/backup/99999/delete/")
        self.assertEqual(resp.status_code, 302)

    def test_get_not_allowed(self):
        resp = self.client.get(f"/instance/{self.config.slug}/backup/{self.backup_record.pk}/delete/")
        self.assertEqual(resp.status_code, 405)


@override_settings(REQUIRE_AUTH=False)
class BulkActionTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
        )
        with patch("backup.services.retention_service.apply_retention"):
            self.b1 = create_backup(config=self.config, trigger="manual")
            self.flows_file.write_text(json.dumps(SAMPLE_FLOWS + [{"id": "x"}]))
            self.b2 = create_backup(config=self.config, trigger="manual")
            self.flows_file.write_text(json.dumps(SAMPLE_FLOWS + [{"id": "y"}]))
            self.b3 = create_backup(config=self.config, trigger="manual")

    def _post(self, data):
        return self.client.post(
            f"/api/instance/{self.config.slug}/bulk/",
            json.dumps(data),
            content_type="application/json",
        )

    def test_bulk_pin(self):
        resp = self._post({"ids": [self.b1.pk, self.b2.pk], "action": "pin"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["affected"], 2)
        self.assertTrue(BackupRecord.objects.get(pk=self.b1.pk).is_pinned)
        self.assertTrue(BackupRecord.objects.get(pk=self.b2.pk).is_pinned)
        self.assertFalse(BackupRecord.objects.get(pk=self.b3.pk).is_pinned)

    def test_bulk_unpin(self):
        self.b1.is_pinned = True
        self.b1.save()
        self.b2.is_pinned = True
        self.b2.save()
        resp = self._post({"ids": [self.b1.pk, self.b2.pk], "action": "unpin"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(BackupRecord.objects.get(pk=self.b1.pk).is_pinned)
        self.assertFalse(BackupRecord.objects.get(pk=self.b2.pk).is_pinned)

    def test_bulk_delete(self):
        p1 = Path(self.b1.file_path)
        p2 = Path(self.b2.file_path)
        resp = self._post({"ids": [self.b1.pk, self.b2.pk], "action": "delete"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["affected"], 2)
        self.assertFalse(BackupRecord.objects.filter(pk=self.b1.pk).exists())
        self.assertFalse(BackupRecord.objects.filter(pk=self.b2.pk).exists())
        self.assertTrue(BackupRecord.objects.filter(pk=self.b3.pk).exists())
        self.assertFalse(p1.is_file())
        self.assertFalse(p2.is_file())

    def test_invalid_action(self):
        resp = self._post({"ids": [self.b1.pk], "action": "nope"})
        self.assertEqual(resp.status_code, 400)

    def test_empty_ids(self):
        resp = self._post({"ids": [], "action": "pin"})
        self.assertEqual(resp.status_code, 400)

    def test_missing_backup_returns_error(self):
        resp = self._post({"ids": [self.b1.pk, 99999], "action": "pin"})
        data = resp.json()
        self.assertEqual(data["affected"], 1)
        self.assertEqual(len(data["errors"]), 1)
        self.assertIn("99999", data["errors"][0])

    def test_get_not_allowed(self):
        resp = self.client.get(f"/api/instance/{self.config.slug}/bulk/")
        self.assertEqual(resp.status_code, 405)
