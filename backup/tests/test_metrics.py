from importlib import reload

from django.test import TestCase, override_settings
from django.urls import Resolver404, clear_url_caches, resolve
from django.utils import timezone

import config.urls
from backup import urls as backup_urls
from backup.models import BackupRecord, NodeRedConfig, RestoreRecord


@override_settings(REQUIRE_AUTH=False)
class MetricsEndpointTest(TestCase):
    def test_returns_200(self):
        resp = self.client.get("/metrics")
        self.assertEqual(resp.status_code, 200)

    def test_exposition_content_type(self):
        resp = self.client.get("/metrics")
        # Prometheus text format — content type starts with text/plain
        self.assertTrue(resp["Content-Type"].startswith("text/plain"))

    def test_django_prometheus_auto_metrics_present(self):
        resp = self.client.get("/metrics")
        body = resp.content.decode()
        # django-prometheus emits at least a python_info process metric.
        self.assertIn("python_info", body)

    def test_flowhistory_custom_metrics_present(self):
        resp = self.client.get("/metrics")
        body = resp.content.decode()
        expected = [
            "flowhistory_backups",
            "flowhistory_backup_bytes",
            "flowhistory_last_successful_backup_timestamp_seconds",
            "flowhistory_instance_enabled",
            "flowhistory_instance_has_error",
            "flowhistory_restores",
            "flowhistory_pinned_backups",
        ]
        for name in expected:
            self.assertIn(name, body, f"missing metric {name}")


@override_settings(REQUIRE_AUTH=True, APP_PASSWORD="secret")
class MetricsAuthBypassTest(TestCase):
    def test_accessible_without_login(self):
        """Scrapers can't do form login — /metrics must bypass SimpleAuthMiddleware."""
        resp = self.client.get("/metrics")
        self.assertEqual(resp.status_code, 200)

    def test_other_paths_still_require_auth(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp["Location"])


@override_settings(REQUIRE_AUTH=False)
class MetricsReflectDatabaseStateTest(TestCase):
    def setUp(self):
        self.config = NodeRedConfig.objects.create(
            name="Home",
            slug="home",
            source_type="local",
            is_enabled=True,
            last_successful_backup=timezone.now(),
        )
        BackupRecord.objects.create(
            config=self.config,
            filename="a.tar.gz",
            file_path="/tmp/a.tar.gz",
            file_size=1234,
            status="success",
            trigger="manual",
        )
        BackupRecord.objects.create(
            config=self.config,
            filename="b.tar.gz",
            file_path="/tmp/b.tar.gz",
            file_size=5678,
            status="success",
            trigger="scheduled",
            is_pinned=True,
        )
        BackupRecord.objects.create(
            config=self.config,
            filename="c.tar.gz",
            file_path="/tmp/c.tar.gz",
            file_size=0,
            status="failed",
            trigger="scheduled",
            error_message="boom",
        )
        RestoreRecord.objects.create(config=self.config, status="success")

    def test_backup_counts_by_status(self):
        body = self.client.get("/metrics").content.decode()
        self.assertIn(
            'flowhistory_backups{instance="home",status="success"} 2.0', body
        )
        self.assertIn(
            'flowhistory_backups{instance="home",status="failed"} 1.0', body
        )

    def test_backup_bytes(self):
        body = self.client.get("/metrics").content.decode()
        self.assertIn('flowhistory_backup_bytes{instance="home"} 6912.0', body)

    def test_pinned_count(self):
        body = self.client.get("/metrics").content.decode()
        self.assertIn('flowhistory_pinned_backups{instance="home"} 1.0', body)

    def test_restore_counts(self):
        body = self.client.get("/metrics").content.decode()
        self.assertIn(
            'flowhistory_restores{instance="home",status="success"} 1.0', body
        )

    def test_instance_enabled_flag(self):
        body = self.client.get("/metrics").content.decode()
        self.assertIn('flowhistory_instance_enabled{instance="home"} 1.0', body)

    def test_instance_error_flag_reflects_last_backup_error(self):
        self.config.last_backup_error = "connection refused"
        self.config.save()
        body = self.client.get("/metrics").content.decode()
        self.assertIn('flowhistory_instance_has_error{instance="home"} 1.0', body)

    def test_label_cardinality_bounds(self):
        """Only 'instance' and 'status' labels should appear on flowhistory_ metrics.

        Guards against leaking filenames, error text, or paths into labels.
        """
        body = self.client.get("/metrics").content.decode()
        allowed_labels = {"instance", "status"}
        for line in body.splitlines():
            if not line.startswith("flowhistory_"):
                continue
            if "{" not in line:
                continue
            label_block = line[line.index("{") + 1 : line.index("}")]
            if not label_block:
                continue
            for pair in label_block.split(","):
                key = pair.split("=", 1)[0]
                self.assertIn(
                    key,
                    allowed_labels,
                    f"Unexpected label {key!r} on metric line: {line}",
                )


class MetricsDisabledTest(TestCase):
    """METRICS_ENABLED=false must unmap the /metrics URL.

    The flag is read at import time in backup/urls.py to extend urlpatterns,
    so override_settings alone is not enough — force a URLconf rebuild under
    the override, then restore it once the override exits. We assert against
    the URL resolver directly because this project's custom_404 handler
    (config/urls.py) redirects unmapped paths to the dashboard, so an HTTP
    302 is indistinguishable from a matched route doing its own redirect.
    """

    @staticmethod
    def _rebuild_urls():
        reload(backup_urls)
        reload(config.urls)
        clear_url_caches()

    def test_url_unmapped_when_disabled(self):
        try:
            with override_settings(METRICS_ENABLED=False):
                self._rebuild_urls()
                with self.assertRaises(Resolver404):
                    resolve("/metrics")
        finally:
            # Restore URLs under the original METRICS_ENABLED so sibling tests
            # still see /metrics registered.
            self._rebuild_urls()

    def test_url_mapped_when_enabled(self):
        # Sanity check: confirms the reload/restore mechanism itself works.
        try:
            with override_settings(METRICS_ENABLED=True):
                self._rebuild_urls()
                match = resolve("/metrics")
                self.assertIsNotNone(match)
        finally:
            self._rebuild_urls()
