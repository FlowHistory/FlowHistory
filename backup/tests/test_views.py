import json

from django.test import TestCase, override_settings

from backup.models import NodeRedConfig
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
