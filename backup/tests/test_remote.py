import hashlib
import json
from unittest.mock import MagicMock, patch

import requests as http_requests

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
                mock_backup.return_value = MagicMock(
                    status="success", filename="test.tar.gz"
                )
                poller.poll_once()
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

    def test_auth_failure_triggers_extended_backoff(self):
        config = NodeRedConfig.objects.create(
            name="Auth Backoff",
            source_type="remote",
            nodered_url="http://fake:1880",
            poll_interval_seconds=30,
        )

        from backup.services.remote_service import AUTH_BACKOFF_SECONDS, RemotePoller

        poller = RemotePoller(config.pk)
        poller._auth_failure = True
        self.assertEqual(poller.get_poll_interval(config), AUTH_BACKOFF_SECONDS)

    def test_successful_poll_resets_failures(self):
        config = NodeRedConfig.objects.create(
            name="Reset Test",
            source_type="remote",
            nodered_url="http://fake:1880",
            watch_enabled=True,
        )

        from backup.services.remote_service import RemotePoller

        poller = RemotePoller(config.pk)
        poller._consecutive_failures = 5
        poller._auth_failure = True

        flows_json = json.dumps([{"id": "tab1", "type": "tab"}])
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = flows_json
        mock_resp.content = flows_json.encode()
        mock_resp.raise_for_status = MagicMock()

        with patch("backup.services.remote_service.requests") as mock_requests:
            mock_requests.get.return_value = mock_resp
            with patch("backup.services.backup_service.create_backup") as mock_backup:
                mock_backup.return_value = MagicMock(
                    status="success", filename="test.tar.gz"
                )
                poller.poll_once()

        self.assertEqual(poller._consecutive_failures, 0)
        self.assertFalse(poller._auth_failure)


class AuthenticateNoderedTest(TestCase):
    @patch("backup.services.remote_service.requests")
    def test_returns_token_on_success(self, mock_requests):
        from backup.services.remote_service import authenticate_nodered

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"access_token": "tok123"}
        mock_resp.raise_for_status = MagicMock()
        mock_requests.post.return_value = mock_resp

        token = authenticate_nodered("http://fake:1880", "admin", "pass")
        self.assertEqual(token, "tok123")
        mock_requests.post.assert_called_once()

    def test_returns_none_without_credentials(self):
        from backup.services.remote_service import authenticate_nodered

        token = authenticate_nodered("http://fake:1880", "", "pass")
        self.assertIsNone(token)

    @patch("backup.services.remote_service.requests")
    def test_raises_on_failure(self, mock_requests):
        from backup.services.remote_service import authenticate_nodered

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = http_requests.HTTPError("401")
        mock_requests.post.return_value = mock_resp

        with self.assertRaises(http_requests.HTTPError):
            authenticate_nodered("http://fake:1880", "admin", "wrong")


class FetchRemoteFlowsTest(TestCase):
    def setUp(self):
        self.config = NodeRedConfig.objects.create(
            name="Fetch Test",
            source_type="remote",
            nodered_url="http://fake:1880",
        )

    @patch("backup.services.remote_service.requests")
    @patch("backup.services.remote_service.authenticate_nodered")
    def test_returns_flows_on_success(self, mock_auth, mock_requests):
        from backup.services.remote_service import fetch_remote_flows

        mock_auth.return_value = None
        flows = json.dumps([{"id": "t1", "type": "tab"}])
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = flows
        mock_resp.content = flows.encode()
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        text, token = fetch_remote_flows(self.config)
        self.assertEqual(text, flows)

    @patch("backup.services.remote_service.requests")
    @patch("backup.services.remote_service.authenticate_nodered")
    def test_retries_on_401(self, mock_auth, mock_requests):
        from backup.services.remote_service import fetch_remote_flows

        mock_auth.return_value = "new_token"
        flows = json.dumps([{"id": "t1", "type": "tab"}])

        # First call returns 401, second succeeds
        mock_resp_401 = MagicMock()
        mock_resp_401.status_code = 401
        mock_resp_ok = MagicMock()
        mock_resp_ok.status_code = 200
        mock_resp_ok.text = flows
        mock_resp_ok.content = flows.encode()
        mock_resp_ok.raise_for_status = MagicMock()
        mock_requests.get.side_effect = [mock_resp_401, mock_resp_ok]

        text, token = fetch_remote_flows(self.config, token="expired_token")
        self.assertEqual(text, flows)
        self.assertEqual(mock_requests.get.call_count, 2)

    @patch("backup.services.remote_service.requests")
    @patch("backup.services.remote_service.authenticate_nodered")
    def test_enforces_size_limit(self, mock_auth, mock_requests):
        from backup.services.remote_service import MAX_RESPONSE_BYTES, fetch_remote_flows

        mock_auth.return_value = None
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"x" * (MAX_RESPONSE_BYTES + 1)
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        with self.assertRaises(ValueError):
            fetch_remote_flows(self.config)


class DeployRemoteFlowsTest(TestCase):
    def setUp(self):
        self.config = NodeRedConfig.objects.create(
            name="Deploy Test",
            source_type="remote",
            nodered_url="http://fake:1880",
        )

    @patch("backup.services.remote_service.requests")
    @patch("backup.services.remote_service.authenticate_nodered")
    def test_deploys_flows(self, mock_auth, mock_requests):
        from backup.services.remote_service import deploy_remote_flows

        mock_auth.return_value = "tok"
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_resp.raise_for_status = MagicMock()
        mock_requests.post.return_value = mock_resp

        deploy_remote_flows(self.config, '[]')
        mock_requests.post.assert_called_once()
        call_kwargs = mock_requests.post.call_args
        self.assertEqual(call_kwargs.kwargs["data"], "[]")

    @patch("backup.services.remote_service.requests")
    @patch("backup.services.remote_service.authenticate_nodered")
    def test_retries_on_401(self, mock_auth, mock_requests):
        from backup.services.remote_service import deploy_remote_flows

        mock_auth.return_value = "new_tok"
        mock_resp_401 = MagicMock()
        mock_resp_401.status_code = 401
        mock_resp_ok = MagicMock()
        mock_resp_ok.status_code = 204
        mock_resp_ok.raise_for_status = MagicMock()
        mock_requests.post.side_effect = [mock_resp_401, mock_resp_ok]

        deploy_remote_flows(self.config, '[]')
        self.assertEqual(mock_requests.post.call_count, 2)

    @patch("backup.services.remote_service.requests")
    @patch("backup.services.remote_service.authenticate_nodered")
    def test_bytes_input_converted(self, mock_auth, mock_requests):
        from backup.services.remote_service import deploy_remote_flows

        mock_auth.return_value = None
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_resp.raise_for_status = MagicMock()
        mock_requests.post.return_value = mock_resp

        deploy_remote_flows(self.config, b'[]')
        call_kwargs = mock_requests.post.call_args
        self.assertEqual(call_kwargs.kwargs["data"], "[]")
