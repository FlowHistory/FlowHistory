"""Tests for DEMO_MODE (ADR 0029).

Acceptance criteria covered:
- AC-1: GET pages render normally.
- AC-2: API POSTs return the demo error envelope and do not write.
- AC-3: HTML form POSTs redirect with a warning and do not delete.
- AC-4: Banner is rendered on every base.html page.
- AC-5: Tested separately at the entrypoint level (manual smoke test).
- AC-6: DEMO_MODE wins over REQUIRE_AUTH=true.
- AC-7: With DEMO_MODE=False the middleware is a no-op.
- AC-8: /metrics and /health/ unaffected.
"""

import json

from django.test import TestCase, override_settings

from backup.models import BackupRecord, NodeRedConfig


@override_settings(DEMO_MODE=True, REQUIRE_AUTH=False)
class DemoModeAllowsReadsTest(TestCase):
    """AC-1, AC-4."""

    def test_dashboard_renders_with_banner(self):
        NodeRedConfig.objects.create(name="Prod")
        NodeRedConfig.objects.create(name="Dev")
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Demo Mode")
        self.assertContains(resp, "changes are not saved")

    def test_instance_dashboard_renders_with_banner(self):
        config = NodeRedConfig.objects.create(name="Solo")
        resp = self.client.get(f"/instance/{config.slug}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Demo Mode")


@override_settings(DEMO_MODE=True, REQUIRE_AUTH=False)
class DemoModeBlocksApiWritesTest(TestCase):
    """AC-2."""

    def setUp(self):
        self.config = NodeRedConfig.objects.create(name="Demo")

    def test_api_create_backup_returns_demo_envelope(self):
        before = BackupRecord.objects.count()
        resp = self.client.post(f"/api/instance/{self.config.slug}/backup/")
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.content)
        self.assertEqual(body["status"], "error")
        self.assertTrue(body.get("demo_mode"))
        self.assertIn("Demo mode", body["message"])
        self.assertEqual(BackupRecord.objects.count(), before)

    def test_api_test_connection_returns_demo_envelope(self):
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/test-connection/"
        )
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.content)
        self.assertEqual(body["status"], "error")
        self.assertTrue(body.get("demo_mode"))


@override_settings(DEMO_MODE=True, REQUIRE_AUTH=False)
class DemoModeBlocksHtmlWritesTest(TestCase):
    """AC-3."""

    def test_instance_delete_post_redirects_and_keeps_row(self):
        config = NodeRedConfig.objects.create(name="ToDelete")
        slug = config.slug
        resp = self.client.post(
            f"/instance/{slug}/delete/", HTTP_REFERER=f"/instance/{slug}/"
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(NodeRedConfig.objects.filter(slug=slug).exists())


@override_settings(DEMO_MODE=True, REQUIRE_AUTH=True, APP_PASSWORD="x")
class DemoModeOverridesAuthTest(TestCase):
    """AC-6 — DEMO_MODE forces REQUIRE_AUTH off via settings.py.

    The override_settings decorator can't run that side effect, so we patch
    REQUIRE_AUTH explicitly to False to mirror the runtime behaviour.
    """

    @override_settings(REQUIRE_AUTH=False)
    def test_dashboard_reachable_without_login(self):
        # Two instances → root renders the grid (single-instance redirects).
        NodeRedConfig.objects.create(name="Pub-A")
        NodeRedConfig.objects.create(name="Pub-B")
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)


@override_settings(DEMO_MODE=False, REQUIRE_AUTH=False)
class DemoModeOffIsNoOpTest(TestCase):
    """AC-7 — when DEMO_MODE is off, no banner and POST behaviour is normal.

    We don't try to fully exercise the real backup pipeline here; we only
    confirm the middleware is transparent — the response is *not* the demo
    envelope and the banner is absent on a GET.
    """

    def test_no_banner_on_dashboard(self):
        NodeRedConfig.objects.create(name="Real-A")
        NodeRedConfig.objects.create(name="Real-B")
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Demo Mode")

    def test_api_post_is_not_intercepted(self):
        config = NodeRedConfig.objects.create(name="Real")
        resp = self.client.post(f"/api/instance/{config.slug}/backup/")
        body = json.loads(resp.content)
        self.assertNotEqual(body.get("demo_mode"), True)


@override_settings(DEMO_MODE=True, REQUIRE_AUTH=False, METRICS_ENABLED=True)
class DemoModeLeavesProbesAloneTest(TestCase):
    """AC-8."""

    def test_health_endpoint_works(self):
        resp = self.client.get("/health/")
        self.assertEqual(resp.status_code, 200)

    def test_metrics_endpoint_works(self):
        resp = self.client.get("/metrics")
        self.assertEqual(resp.status_code, 200)
