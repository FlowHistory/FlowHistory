import json
import re
from unittest.mock import patch

from django.test import TestCase, override_settings

from backup.models import NodeRedConfig, RestoreRecord
from backup.services.backup_service import create_backup
from backup.tests.helpers import SAMPLE_FLOWS, TempBackupDirMixin


@override_settings(REQUIRE_AUTH=False)
class AggregateDashboardTest(TestCase):
    def test_no_instances_redirects_to_add(self):
        resp = self.client.get("/")
        self.assertRedirects(resp, "/instance/add/")

    def test_single_instance_redirects_to_dashboard(self):
        config = NodeRedConfig.objects.create(name="Solo")
        resp = self.client.get("/")
        self.assertRedirects(resp, f"/instance/{config.slug}/")

    def test_multiple_instances_shows_grid(self):
        NodeRedConfig.objects.create(name="Prod")
        NodeRedConfig.objects.create(name="Dev")
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Prod")
        self.assertContains(resp, "Dev")


@override_settings(REQUIRE_AUTH=False)
class InstanceIsolationTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_a = self.backup_dir / "a" / "flows.json"
        self.flows_a.parent.mkdir()
        self.flows_a.write_text(json.dumps(SAMPLE_FLOWS))

        self.flows_b = self.backup_dir / "b" / "flows.json"
        self.flows_b.parent.mkdir()
        self.flows_b.write_text(json.dumps([{"id": "x", "type": "tab", "label": "X"}]))

        self.config_a = NodeRedConfig.objects.create(
            name="Instance A", flows_path=str(self.flows_a)
        )
        self.config_b = NodeRedConfig.objects.create(
            name="Instance B", flows_path=str(self.flows_b)
        )

    def test_backups_isolated_between_instances(self):
        from pathlib import Path

        rec_a = create_backup(self.config_a, trigger="manual")
        rec_b = create_backup(self.config_b, trigger="manual")
        self.assertEqual(rec_a.config, self.config_a)
        self.assertEqual(rec_b.config, self.config_b)
        self.assertNotEqual(
            Path(rec_a.file_path).parent,
            Path(rec_b.file_path).parent,
        )

    def test_backup_dirs_use_slug(self):
        rec = create_backup(self.config_a, trigger="manual")
        self.assertIn(self.config_a.slug, rec.file_path)

    def test_instance_dashboard_only_shows_own_backups(self):
        create_backup(self.config_a, trigger="manual")
        create_backup(self.config_b, trigger="manual")

        resp = self.client.get(f"/instance/{self.config_a.slug}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["backup_count"], 1)

    def test_cannot_access_other_instance_backup(self):
        rec_b = create_backup(self.config_b, trigger="manual")
        # Try to access config_b's backup via config_a's URL
        resp = self.client.get(f"/instance/{self.config_a.slug}/backup/{rec_b.pk}/")
        self.assertEqual(resp.status_code, 302)  # redirect (not found)


@override_settings(REQUIRE_AUTH=False)
class InstanceDeleteTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            name="To Delete",
            flows_path=str(self.flows_file),
        )
        create_backup(self.config, trigger="manual")

    def test_get_shows_confirmation(self):
        resp = self.client.get(f"/instance/{self.config.slug}/delete/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "To Delete")
        self.assertContains(resp, "1")  # backup count

    def test_post_deletes_instance(self):
        slug = self.config.slug
        resp = self.client.post(f"/instance/{slug}/delete/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/")
        self.assertFalse(NodeRedConfig.objects.filter(slug=slug).exists())

    def test_post_with_delete_files(self):
        slug = self.config.slug
        backup_dir = self.config.backup_dir
        self.assertTrue(backup_dir.is_dir())
        resp = self.client.post(f"/instance/{slug}/delete/", {"delete_files": "on"})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(backup_dir.is_dir())


@override_settings(REQUIRE_AUTH=False)
class BackupDetailViewTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            name="Detail Test",
            flows_path=str(self.flows_file),
        )

    def test_renders_backup_detail(self):
        rec = create_backup(self.config, trigger="manual")
        resp = self.client.get(f"/instance/{self.config.slug}/backup/{rec.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["backup"], rec)

    def test_nonexistent_backup_redirects(self):
        resp = self.client.get(f"/instance/{self.config.slug}/backup/9999/")
        self.assertEqual(resp.status_code, 302)

    def test_shows_previous_backup(self):
        rec1 = create_backup(self.config, trigger="manual")
        # Modify flows so dedup doesn't skip
        self.flows_file.write_text(
            json.dumps([{"id": "tab1", "type": "tab", "label": "Changed"}])
        )
        rec2 = create_backup(self.config, trigger="manual")
        resp = self.client.get(f"/instance/{self.config.slug}/backup/{rec2.pk}/")
        self.assertEqual(resp.context["prev_backup"], rec1)

    def test_shows_restore_history(self):
        rec = create_backup(self.config, trigger="manual")
        RestoreRecord.objects.create(config=self.config, backup=rec, status="success")
        resp = self.client.get(f"/instance/{self.config.slug}/backup/{rec.pk}/")
        self.assertEqual(resp.context["restores"].count(), 1)


@override_settings(REQUIRE_AUTH=False)
class BackupDownloadViewTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            name="Download Test",
            flows_path=str(self.flows_file),
        )

    def test_downloads_archive(self):
        rec = create_backup(self.config, trigger="manual")
        resp = self.client.get(
            f"/instance/{self.config.slug}/backup/{rec.pk}/download/"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/gzip")

    def test_missing_archive_returns_404(self):
        from pathlib import Path

        rec = create_backup(self.config, trigger="manual")
        Path(rec.file_path).unlink()
        resp = self.client.get(
            f"/instance/{self.config.slug}/backup/{rec.pk}/download/"
        )
        self.assertEqual(resp.status_code, 404)

    def test_nonexistent_record_redirects(self):
        resp = self.client.get(f"/instance/{self.config.slug}/backup/9999/download/")
        self.assertEqual(resp.status_code, 302)


@override_settings(REQUIRE_AUTH=False)
class InstanceSettingsViewTest(TestCase):
    def test_renders_settings_page(self):
        config = NodeRedConfig.objects.create(name="Settings Test")
        resp = self.client.get(f"/instance/{config.slug}/settings/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["config"], config)
        self.assertIn("notification_backends", resp.context)
        self.assertIn("notify_backend_status", resp.context)


@override_settings(REQUIRE_AUTH=False)
class ApiTestConnectionTest(TestCase):
    def setUp(self):
        self.local_config = NodeRedConfig.objects.create(
            name="Local", source_type="local"
        )
        self.remote_config = NodeRedConfig.objects.create(
            name="Remote",
            source_type="remote",
            nodered_url="http://fake:1880",
        )

    def test_local_instance_returns_400(self):
        resp = self.client.post(
            f"/api/instance/{self.local_config.slug}/test-connection/"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Not a remote instance", resp.json()["message"])

    def test_no_url_returns_400(self):
        self.remote_config.nodered_url = ""
        self.remote_config.save()
        resp = self.client.post(
            f"/api/instance/{self.remote_config.slug}/test-connection/"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("No URL configured", resp.json()["message"])

    @patch("backup.services.remote_service.fetch_remote_flows")
    def test_successful_connection(self, mock_fetch):
        flows = [{"id": "t1", "type": "tab"}, {"id": "n1", "type": "inject", "z": "t1"}]
        mock_fetch.return_value = (json.dumps(flows), "token123")
        resp = self.client.post(
            f"/api/instance/{self.remote_config.slug}/test-connection/"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("2 flow objects", resp.json()["message"])

    @patch("backup.services.remote_service.fetch_remote_flows")
    def test_connection_error_returns_502(self, mock_fetch):
        import requests as http_requests

        mock_fetch.side_effect = http_requests.ConnectionError("refused")
        resp = self.client.post(
            f"/api/instance/{self.remote_config.slug}/test-connection/"
        )
        self.assertEqual(resp.status_code, 502)

    @patch("backup.services.remote_service.fetch_remote_flows")
    def test_timeout_returns_504(self, mock_fetch):
        import requests as http_requests

        mock_fetch.side_effect = http_requests.Timeout("timed out")
        resp = self.client.post(
            f"/api/instance/{self.remote_config.slug}/test-connection/"
        )
        self.assertEqual(resp.status_code, 504)

    @patch("backup.services.remote_service.fetch_remote_flows")
    def test_generic_error_returns_500(self, mock_fetch):
        mock_fetch.side_effect = RuntimeError("unexpected")
        resp = self.client.post(
            f"/api/instance/{self.remote_config.slug}/test-connection/"
        )
        self.assertEqual(resp.status_code, 500)


@override_settings(REQUIRE_AUTH=False)
class DashboardDualRenderTest(TempBackupDirMixin, TestCase):
    """ADR 0028: backup history must render as both desktop table and mobile cards."""

    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            name="Dual Render", flows_path=str(self.flows_file)
        )
        self.rec1 = create_backup(self.config, trigger="manual")
        self.flows_file.write_text(
            json.dumps([{"id": "t", "type": "tab", "label": "Changed"}])
        )
        self.rec2 = create_backup(self.config, trigger="manual")

    def _get_dashboard(self):
        resp = self.client.get(f"/instance/{self.config.slug}/")
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def test_renders_desktop_table_block(self):
        html = self._get_dashboard()
        # Match the wrapper <div> by its responsive classes, tolerant of class reorder.
        self.assertRegex(
            html,
            r'<div\s+class="[^"]*\bhidden\b[^"]*\bmd:block\b[^"]*"'
            r'|<div\s+class="[^"]*\bmd:block\b[^"]*\bhidden\b[^"]*"',
        )
        self.assertIn("<table", html)

    def test_renders_mobile_card_block(self):
        html = self._get_dashboard()
        self.assertRegex(html, r'<div\s+class="[^"]*\bmd:hidden\b[^"]*"')
        self.assertIn("select-all-mobile", html)

    def test_each_backup_has_two_checkboxes_with_same_value(self):
        html = self._get_dashboard()
        input_tags = re.findall(r"<input\b[^>]*>", html)
        for rec in (self.rec1, self.rec2):
            matching = [
                tag
                for tag in input_tags
                if "backup-checkbox" in tag and f'value="{rec.pk}"' in tag
            ]
            self.assertEqual(
                len(matching),
                2,
                f"Backup {rec.pk} should have exactly 2 .backup-checkbox elements",
            )

    def test_actions_partial_used_by_both_layouts(self):
        # Each backup's Delete handler call appears once per layout = twice total.
        html = self._get_dashboard()
        for rec in (self.rec1, self.rec2):
            occurrences = html.count(f"deleteBackup({rec.pk}")
            self.assertEqual(
                occurrences,
                2,
                f"Backup {rec.pk} delete action should appear twice (table + card)",
            )

    def test_empty_state_in_both_layouts(self):
        empty_config = NodeRedConfig.objects.create(
            name="Empty", flows_path=str(self.flows_file)
        )
        resp = self.client.get(f"/instance/{empty_config.slug}/")
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("No backups yet", html)
