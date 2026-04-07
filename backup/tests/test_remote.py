import hashlib
import json
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from backup.models import NodeRedConfig


@override_settings(REQUIRE_AUTH=False)
class RemotePollerTest(TestCase):
    def test_poll_once_detects_change(self):
        config = NodeRedConfig.objects.create(
            name="Remote Test",
            source_type="remote",
            nodered_url="http://fake:1880",
            watch_enabled=True,
        )

        from backup.services.remote_service import RemotePoller

        poller = RemotePoller(config.pk)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps([{"id": "tab1", "type": "tab", "label": "Test"}])
        mock_resp.json.return_value = [{"id": "tab1", "type": "tab", "label": "Test"}]
        mock_resp.raise_for_status = MagicMock()

        with patch("backup.services.remote_service.requests") as mock_requests:
            mock_requests.get.return_value = mock_resp
            with patch("backup.services.backup_service.create_backup") as mock_backup:
                mock_backup.return_value = MagicMock(status="success", filename="test.tar.gz")
                result = poller.poll_once()
                self.assertTrue(mock_backup.called)

    def test_poll_once_skips_unchanged(self):
        config = NodeRedConfig.objects.create(
            name="Remote Unchanged",
            source_type="remote",
            nodered_url="http://fake:1880",
            watch_enabled=True,
        )

        from backup.services.remote_service import RemotePoller

        poller = RemotePoller(config.pk)
        flows_json = json.dumps([{"id": "tab1", "type": "tab"}])
        poller._last_checksum = hashlib.sha256(flows_json.encode()).hexdigest()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = flows_json
        mock_resp.raise_for_status = MagicMock()

        with patch("backup.services.remote_service.requests") as mock_requests:
            mock_requests.get.return_value = mock_resp
            result = poller.poll_once()
            self.assertFalse(result)  # No change

    def test_backoff_increases_interval(self):
        config = NodeRedConfig.objects.create(
            name="Backoff Test",
            source_type="remote",
            nodered_url="http://fake:1880",
            poll_interval_seconds=30,
        )

        from backup.services.remote_service import RemotePoller

        poller = RemotePoller(config.pk)
        self.assertEqual(poller.get_poll_interval(config), 30)

        poller._consecutive_failures = 3  # At threshold
        self.assertEqual(poller.get_poll_interval(config), 60)  # Doubled

        poller._consecutive_failures = 4
        self.assertEqual(poller.get_poll_interval(config), 120)

        poller._consecutive_failures = 10
        self.assertLessEqual(poller.get_poll_interval(config), 300)  # Capped
