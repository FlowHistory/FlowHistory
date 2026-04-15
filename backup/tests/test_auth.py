from unittest.mock import patch

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings


@override_settings(REQUIRE_AUTH=False)
class HealthCheckTest(TestCase):
    def test_returns_ok(self):
        resp = self.client.get("/health/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})


@override_settings(REQUIRE_AUTH=True, APP_PASSWORD="secret123")
class LoginViewTest(TestCase):
    def test_get_renders_form(self):
        resp = self.client.get("/login/")
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "backup/login.html")

    def test_correct_password_redirects_to_dashboard(self):
        resp = self.client.post("/login/", {"password": "secret123"})
        self.assertRedirects(resp, "/", fetch_redirect_response=False)
        self.assertTrue(self.client.session["authenticated"])

    def test_wrong_password_shows_error(self):
        resp = self.client.post("/login/", {"password": "wrong"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Invalid password")


@override_settings(REQUIRE_AUTH=True, APP_PASSWORD="secret123")
class LogoutViewTest(TestCase):
    def test_post_flushes_session(self):
        # Log in first
        self.client.post("/login/", {"password": "secret123"})
        self.assertTrue(self.client.session.get("authenticated"))
        # Log out
        resp = self.client.post("/logout/")
        self.assertRedirects(resp, "/login/", fetch_redirect_response=False)
        self.assertFalse(self.client.session.get("authenticated", False))


@override_settings(REQUIRE_AUTH=True, APP_PASSWORD="secret123")
class SimpleAuthMiddlewareTest(TestCase):
    def test_bypass_when_auth_disabled(self):
        with self.settings(REQUIRE_AUTH=False):
            resp = self.client.get("/")
            # Should not redirect to login (will redirect to add or dashboard)
            self.assertNotEqual(resp.url if resp.status_code == 302 else "", "/login/")

    def test_exempt_paths_allowed(self):
        resp = self.client.get("/login/")
        self.assertEqual(resp.status_code, 200)

        resp = self.client.get("/health/")
        self.assertEqual(resp.status_code, 200)

    def test_authenticated_session_passes(self):
        self.client.post("/login/", {"password": "secret123"})
        resp = self.client.get("/")
        # Should not redirect to login
        self.assertNotEqual(resp.url if resp.status_code == 302 else "", "/login/")

    def test_unauthenticated_redirects_to_login(self):
        resp = self.client.get("/")
        self.assertRedirects(resp, "/login/", fetch_redirect_response=False)

    def test_unauthenticated_api_redirects_to_login(self):
        resp = self.client.get("/instance/test/settings/")
        self.assertRedirects(resp, "/login/", fetch_redirect_response=False)


@override_settings(REQUIRE_AUTH=True, APP_PASSWORD="secret123")
class RateLimitTest(TestCase):
    def setUp(self):
        from backup.middleware.simple_auth import _failed_attempts

        _failed_attempts.clear()

    def tearDown(self):
        from backup.middleware.simple_auth import _failed_attempts

        _failed_attempts.clear()

    def test_blocks_after_max_attempts(self):
        for _ in range(5):
            resp = self.client.post("/login/", {"password": "wrong"})
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, "Invalid password")

        # 6th attempt should be blocked by middleware
        resp = self.client.post("/login/", {"password": "wrong"})
        self.assertContains(resp, "Too many failed attempts")

    def test_correct_password_still_rejected_when_locked_out(self):
        for _ in range(5):
            self.client.post("/login/", {"password": "wrong"})

        resp = self.client.post("/login/", {"password": "secret123"})
        self.assertContains(resp, "Too many failed attempts")
        self.assertFalse(self.client.session.get("authenticated", False))

    def test_successful_login_clears_attempts(self):
        for _ in range(3):
            self.client.post("/login/", {"password": "wrong"})

        resp = self.client.post("/login/", {"password": "secret123"})
        self.assertRedirects(resp, "/", fetch_redirect_response=False)

        # After successful login + logout, failed counter should be cleared
        self.client.post("/logout/")
        # Should be able to fail again without immediate lockout
        for _ in range(4):
            self.client.post("/login/", {"password": "wrong"})
        resp = self.client.post("/login/", {"password": "wrong"})
        self.assertContains(resp, "Invalid password")

    @patch("backup.middleware.simple_auth.time")
    def test_lockout_expires(self, mock_time):
        mock_time.monotonic.return_value = 1000.0
        for _ in range(5):
            self.client.post("/login/", {"password": "wrong"})

        # Still locked out
        mock_time.monotonic.return_value = 1100.0
        resp = self.client.post("/login/", {"password": "secret123"})
        self.assertContains(resp, "Too many failed attempts")

        # After lockout duration (900s), should be allowed again
        mock_time.monotonic.return_value = 1000.0 + 901.0
        resp = self.client.post("/login/", {"password": "secret123"})
        self.assertRedirects(resp, "/", fetch_redirect_response=False)


class StartupValidationTest(TestCase):
    def _get_app(self):
        return apps.get_app_config("backup")

    @override_settings(REQUIRE_AUTH=True, APP_PASSWORD="")
    def test_raises_on_empty_password(self):
        with self.assertRaises(ImproperlyConfigured):
            self._get_app().ready()

    @override_settings(REQUIRE_AUTH=True, APP_PASSWORD="secret123")
    def test_passes_with_password_set(self):
        self._get_app().ready()  # Should not raise

    @override_settings(REQUIRE_AUTH=False, APP_PASSWORD="")
    def test_passes_when_auth_disabled(self):
        self._get_app().ready()  # Should not raise
