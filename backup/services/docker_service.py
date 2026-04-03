"""Optional Docker container management for Node-RED restart."""

import logging

logger = logging.getLogger(__name__)

try:
    import docker
    from docker.errors import APIError, DockerException, NotFound
except ImportError:
    docker = None
    APIError = None
    DockerException = None
    NotFound = None


def is_docker_available():
    """Check if the Docker SDK is installed and the socket is reachable."""
    if docker is None:
        return False
    try:
        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


def get_container_status(container_name):
    """Return container status dict or None if unavailable."""
    if docker is None:
        return None
    try:
        client = docker.from_env()
        container = client.containers.get(container_name)
        return {
            "name": container.name,
            "status": container.status,
        }
    except Exception:
        return None


def restart_container(container_name, timeout=30):
    """Restart a Docker container by name.

    Returns:
        {"success": bool, "message": str}
    """
    if docker is None:
        return {"success": False, "message": "Docker SDK not installed"}

    try:
        client = docker.from_env()
        container = client.containers.get(container_name)
        container.restart(timeout=timeout)
        logger.info("Restarted container %s", container_name)
        return {"success": True, "message": f"Container '{container_name}' restarted"}
    except NotFound:
        msg = f"Container '{container_name}' not found"
        logger.error(msg)
        return {"success": False, "message": msg}
    except (APIError, DockerException) as e:
        msg = f"Docker error restarting '{container_name}': {e}"
        logger.error(msg)
        return {"success": False, "message": msg}
    except Exception as e:
        msg = f"Unexpected error restarting '{container_name}': {e}"
        logger.error(msg)
        return {"success": False, "message": msg}
