from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase

from backup.models import NodeRedConfig
from backup.tests.helpers import TempBackupDirMixin


class NodeRedConfigModelTest(TempBackupDirMixin, TestCase):
    def test_slug_auto_generated_from_name(self):
        config = NodeRedConfig.objects.create(name="Production")
        self.assertEqual(config.slug, "production")

    def test_slug_dedup(self):
        c1 = NodeRedConfig.objects.create(name="Node-RED")
        c2 = NodeRedConfig.objects.create(name="Node-RED")
        self.assertEqual(c1.slug, "node-red")
        self.assertEqual(c2.slug, "node-red-2")

    def test_reserved_slug_avoided(self):
        config = NodeRedConfig.objects.create(name="Add")
        self.assertEqual(config.slug, "add-instance")

    def test_reserved_slug_api(self):
        config = NodeRedConfig.objects.create(name="Api")
        self.assertEqual(config.slug, "api-instance")

    def test_color_auto_assigned(self):
        config = NodeRedConfig.objects.create(name="First")
        self.assertEqual(config.color, "#3B82F6")

    def test_second_color_different(self):
        c1 = NodeRedConfig.objects.create(name="First")
        c2 = NodeRedConfig.objects.create(name="Second")
        self.assertNotEqual(c1.color, c2.color)
        self.assertEqual(c2.color, "#EF4444")

    def test_backup_dir_property(self):
        config = NodeRedConfig.objects.create(name="Test")
        expected = Path(settings.BACKUP_DIR) / config.slug
        self.assertEqual(config.backup_dir, expected)

    def test_get_nodered_credentials_with_prefix(self):
        config = NodeRedConfig.objects.create(name="Cred Test", env_prefix="MYTEST")
        with patch.dict("os.environ", {
            "FLOWHISTORY_MYTEST_USER": "admin",
            "FLOWHISTORY_MYTEST_PASS": "secret",
        }):
            user, pwd = config.get_nodered_credentials()
            self.assertEqual(user, "admin")
            self.assertEqual(pwd, "secret")

    def test_get_nodered_credentials_no_prefix(self):
        config = NodeRedConfig.objects.create(name="No Prefix")
        user, pwd = config.get_nodered_credentials()
        self.assertIsNone(user)
        self.assertIsNone(pwd)

    def test_schedule_enabled_field(self):
        config = NodeRedConfig.objects.create(name="Sched Test")
        self.assertTrue(config.schedule_enabled)
        config.schedule_enabled = False
        config.save()
        config.refresh_from_db()
        self.assertFalse(config.schedule_enabled)

    def test_explicit_slug_not_overwritten(self):
        config = NodeRedConfig.objects.create(name="Whatever", slug="custom-slug")
        self.assertEqual(config.slug, "custom-slug")

    def test_str_returns_name(self):
        config = NodeRedConfig.objects.create(name="My Instance")
        self.assertEqual(str(config), "My Instance")
