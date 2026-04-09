from unittest.mock import patch

from django.test import TestCase, override_settings

from backup.models import NodeRedConfig


@override_settings(REQUIRE_AUTH=False)
class FooterBuildMetadataTest(TestCase):
    def test_footer_shows_dev_by_default(self):
        NodeRedConfig.objects.create(name="Test")
        resp = self.client.get("/instance/test/")
        self.assertContains(resp, "dev")

    def test_footer_shows_build_info(self):
        NodeRedConfig.objects.create(name="Test")
        env = {
            "GIT_COMMIT_SHORT": "abc1234",
            "BUILD_DATE": "2026-04-09T12:00:00Z",
            "BUILD_REPO": "FlowHistory/FlowHistory",
        }
        with patch.dict("os.environ", env):
            import importlib

            from backup import context_processors

            importlib.reload(context_processors)
            try:
                resp = self.client.get("/instance/test/")
                self.assertContains(resp, "abc1234")
                self.assertContains(resp, "2026-04-09T12:00:00Z")
                self.assertContains(resp, "FlowHistory/FlowHistory")
            finally:
                importlib.reload(context_processors)
