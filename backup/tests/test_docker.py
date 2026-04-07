from unittest.mock import MagicMock, patch

from django.test import TestCase


class DockerServiceTest(TestCase):
    def test_is_docker_available_no_sdk(self):
        with patch("backup.services.docker_service.docker", None):
            from backup.services.docker_service import is_docker_available

            self.assertFalse(is_docker_available())

    def test_restart_container_no_sdk(self):
        with patch("backup.services.docker_service.docker", None):
            from backup.services.docker_service import restart_container

            result = restart_container("nodered")
            self.assertFalse(result["success"])
            self.assertIn("not installed", result["message"])

    def test_restart_container_success(self):
        mock_docker = MagicMock()
        mock_container = MagicMock()
        mock_docker.from_env.return_value.containers.get.return_value = mock_container
        with patch("backup.services.docker_service.docker", mock_docker):
            from backup.services.docker_service import restart_container

            result = restart_container("nodered")
            self.assertTrue(result["success"])
            mock_container.restart.assert_called_once_with(timeout=30)

    def test_restart_container_not_found(self):
        mock_docker = MagicMock()
        from docker.errors import NotFound

        mock_docker.from_env.return_value.containers.get.side_effect = NotFound(
            "not found"
        )
        with (
            patch("backup.services.docker_service.docker", mock_docker),
            patch("backup.services.docker_service.NotFound", NotFound),
        ):
            from backup.services.docker_service import restart_container

            result = restart_container("nodered")
            self.assertFalse(result["success"])
            self.assertIn("not found", result["message"])

    def test_get_container_status_no_sdk(self):
        with patch("backup.services.docker_service.docker", None):
            from backup.services.docker_service import get_container_status

            self.assertIsNone(get_container_status("nodered"))
