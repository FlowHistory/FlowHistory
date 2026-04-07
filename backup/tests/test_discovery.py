import os
from unittest.mock import patch

from django.test import TestCase

from backup.models import NodeRedConfig
from backup.services.discovery_service import discover_instances_from_env


class DiscoveryServiceTest(TestCase):
    def _clean_env(self, env_dict):
        """Return env_dict with only FLOWHISTORY_* keys removed from os.environ."""
        clean = {k: v for k, v in os.environ.items() if not k.startswith("FLOWHISTORY_")}
        clean.update(env_dict)
        return clean

    def test_discover_remote_instance(self):
        env = self._clean_env({"FLOWHISTORY_PROD_URL": "http://192.168.1.50:1880"})
        with patch.dict("os.environ", env, clear=True):
            result = discover_instances_from_env()
        self.assertIn("PROD", result["created"])
        config = NodeRedConfig.objects.get(env_prefix="PROD")
        self.assertEqual(config.source_type, "remote")
        self.assertEqual(config.nodered_url, "http://192.168.1.50:1880")

    def test_discover_local_instance(self):
        env = self._clean_env({
            "FLOWHISTORY_DEV_FLOWS_PATH": "/data/flows.json",
            "FLOWHISTORY_DEV_NAME": "Dev Box",
        })
        with patch.dict("os.environ", env, clear=True):
            result = discover_instances_from_env()
        self.assertIn("DEV", result["created"])
        config = NodeRedConfig.objects.get(env_prefix="DEV")
        self.assertEqual(config.source_type, "local")
        self.assertEqual(config.flows_path, "/data/flows.json")
        self.assertEqual(config.name, "Dev Box")

    def test_skip_existing_prefix(self):
        NodeRedConfig.objects.create(name="Existing", env_prefix="PROD")
        env = self._clean_env({"FLOWHISTORY_PROD_URL": "http://example.com:1880"})
        with patch.dict("os.environ", env, clear=True):
            result = discover_instances_from_env()
        self.assertIn("PROD", result["skipped"])
        self.assertEqual(result["created"], [])

    def test_force_updates_existing(self):
        config = NodeRedConfig.objects.create(
            name="Old Name", env_prefix="PROD", source_type="remote",
            nodered_url="http://old:1880",
        )
        env = self._clean_env({
            "FLOWHISTORY_PROD_URL": "http://new:1880",
            "FLOWHISTORY_PROD_NAME": "New Name",
        })
        with patch.dict("os.environ", env, clear=True):
            result = discover_instances_from_env(force=True)
        self.assertIn("PROD", result["updated"])
        config.refresh_from_db()
        self.assertEqual(config.name, "New Name")
        self.assertEqual(config.nodered_url, "http://new:1880")

    def test_no_env_vars(self):
        env = self._clean_env({})
        with patch.dict("os.environ", env, clear=True):
            result = discover_instances_from_env()
        self.assertEqual(result["created"], [])
        self.assertEqual(result["skipped"], [])
        self.assertEqual(result["updated"], [])

    def test_both_url_and_flows_path_prefers_remote(self):
        env = self._clean_env({
            "FLOWHISTORY_DUAL_URL": "http://example.com:1880",
            "FLOWHISTORY_DUAL_FLOWS_PATH": "/data/flows.json",
        })
        with patch.dict("os.environ", env, clear=True):
            result = discover_instances_from_env()
        self.assertIn("DUAL", result["created"])
        config = NodeRedConfig.objects.get(env_prefix="DUAL")
        self.assertEqual(config.source_type, "remote")

    def test_optional_fields_applied(self):
        env = self._clean_env({
            "FLOWHISTORY_FULL_URL": "http://example.com:1880",
            "FLOWHISTORY_FULL_NAME": "Full Config",
            "FLOWHISTORY_FULL_SCHEDULE": "weekly",
            "FLOWHISTORY_FULL_TIME": "04:30",
            "FLOWHISTORY_FULL_MAX_BACKUPS": "50",
            "FLOWHISTORY_FULL_ALWAYS_BACKUP": "true",
        })
        with patch.dict("os.environ", env, clear=True):
            result = discover_instances_from_env()
        config = NodeRedConfig.objects.get(env_prefix="FULL")
        self.assertEqual(config.backup_frequency, "weekly")
        self.assertEqual(config.max_backups, 50)
        self.assertTrue(config.always_backup)
