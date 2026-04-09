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
