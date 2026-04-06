"""Remote Node-RED API poller for detecting flow changes."""

import hashlib
import json
import logging
import threading
import time

import requests

logger = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 300
AUTH_BACKOFF_SECONDS = 600  # 10 minutes — matches Node-RED's rate limit window
BACKOFF_THRESHOLD = 3
MAX_RESPONSE_BYTES = 10_000_000  # 10 MB safety limit
TOKEN_TTL_SECONDS = 3300  # Refresh cached tokens after 55 minutes


def authenticate_nodered(url, username, password, scope="flows.read"):
    """Authenticate with a Node-RED instance and return the access token.

    Returns None if no credentials provided.
    Raises requests.RequestException on auth failure.
    """
    if not username:
        return None
    if url.startswith("http://"):
        logger.warning("Sending credentials over plaintext HTTP to %s — use HTTPS in production", url)
    resp = requests.post(
        f"{url}/auth/token",
        data={
            "client_id": "node-red-admin",
            "grant_type": "password",
            "scope": scope,
            "username": username,
            "password": password,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("access_token")


def fetch_remote_flows(config, token=None):
    """Fetch flows from a remote Node-RED instance.

    Args:
        config: NodeRedConfig with source_type="remote".
        token: Pre-authenticated bearer token. If None, authenticates fresh.

    Returns:
        Flows JSON string.

    Raises:
        requests.RequestException on connection/auth failure.
    """
    if token is None:
        username, password = config.get_nodered_credentials()
        token = authenticate_nodered(config.nodered_url, username, password)
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    resp = requests.get(
        f"{config.nodered_url}/flows",
        headers=headers,
        timeout=15,
    )

    # If token was cached and expired, re-authenticate once
    if resp.status_code == 401 and token:
        username, password = config.get_nodered_credentials()
        token = authenticate_nodered(config.nodered_url, username, password)
        if token:
            resp = requests.get(
                f"{config.nodered_url}/flows",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )

    resp.raise_for_status()

    if len(resp.content) > MAX_RESPONSE_BYTES:
        raise ValueError(
            f"Flows response too large ({len(resp.content)} bytes, limit {MAX_RESPONSE_BYTES})"
        )

    return resp.text, token


def deploy_remote_flows(config, flows_json):
    """Deploy flows to a remote Node-RED instance via POST /flows.

    Args:
        config: NodeRedConfig with source_type="remote".
        flows_json: Flows as a JSON string or bytes.

    Raises:
        requests.RequestException on connection/auth/deploy failure.
    """
    username, password = config.get_nodered_credentials()
    token = authenticate_nodered(
        config.nodered_url, username, password, scope="flows.read flows.write",
    )
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if isinstance(flows_json, bytes):
        flows_json = flows_json.decode()

    resp = requests.post(
        f"{config.nodered_url}/flows",
        headers=headers,
        data=flows_json,
        timeout=30,
    )

    # Retry once on expired token
    if resp.status_code == 401 and token:
        token = authenticate_nodered(
            config.nodered_url, username, password, scope="flows.read flows.write",
        )
        if token:
            headers["Authorization"] = f"Bearer {token}"
            resp = requests.post(
                f"{config.nodered_url}/flows",
                headers=headers,
                data=flows_json,
                timeout=30,
            )

    resp.raise_for_status()


class RemotePoller:
    """Polls a remote Node-RED instance's Admin API for flow changes."""

    def __init__(self, config_id):
        self._config_id = config_id
        self._last_checksum = None
        self._consecutive_failures = 0
        self._auth_failure = False
        self._cached_token = None
        self._token_acquired_at = 0
        self._lock = threading.Lock()

    def _get_config(self):
        from backup.models import NodeRedConfig
        return NodeRedConfig.objects.get(pk=self._config_id)

    def poll_once(self):
        """Poll the remote API once. Returns True if a backup was triggered."""
        from backup.models import NodeRedConfig
        from backup.services.backup_service import create_backup

        try:
            config = self._get_config()
        except NodeRedConfig.DoesNotExist:
            logger.warning("Remote poller: config %d no longer exists", self._config_id)
            return False

        if not config.watch_enabled:
            return False

        try:
            with self._lock:
                # Expire cached token after TTL
                if self._cached_token and (time.monotonic() - self._token_acquired_at) > TOKEN_TTL_SECONDS:
                    self._cached_token = None
                flows_text, new_token = fetch_remote_flows(config, token=self._cached_token)
                if new_token != self._cached_token:
                    self._token_acquired_at = time.monotonic()
                self._cached_token = new_token
        except Exception as e:
            with self._lock:
                self._consecutive_failures += 1
                if hasattr(e, 'response') and e.response is not None:
                    status = e.response.status_code
                    try:
                        body = e.response.json()
                        reason = body.get("error_description", body.get("error", f"{status} {e.response.reason}"))
                    except Exception:
                        reason = f"{status} {e.response.reason}"
                    if status in (401, 403):
                        self._auth_failure = True
                        self._cached_token = None
                elif isinstance(e, requests.ConnectionError):
                    reason = f"Cannot connect to {config.nodered_url}"
                elif isinstance(e, requests.Timeout):
                    reason = f"Connection to {config.nodered_url} timed out"
                else:
                    reason = str(e)
            level = logging.WARNING if self._consecutive_failures >= BACKOFF_THRESHOLD else logging.ERROR
            logger.log(
                level,
                "Remote poll failed for %s (attempt %d)",
                config.name, self._consecutive_failures,
                exc_info=True,
            )
            config.last_backup_error = reason
            config.save(update_fields=["last_backup_error"])
            return False

        with self._lock:
            self._consecutive_failures = 0
            self._auth_failure = False

        # Clear any previous error on successful fetch
        if config.last_backup_error:
            config.last_backup_error = ""
            config.save(update_fields=["last_backup_error"])

        checksum = hashlib.sha256(flows_text.encode()).hexdigest()
        with self._lock:
            if checksum == self._last_checksum:
                return False
            self._last_checksum = checksum
        logger.info("Remote flow change detected for %s", config.name)

        try:
            result = create_backup(config=config, trigger="file_change", flows_data=flows_text)
            if result is None:
                logger.info("Remote backup skipped for %s — no changes", config.name)
                return False
            if result.status == "success":
                logger.info("Remote backup created for %s: %s", config.name, result.filename)
                return True
            return False
        except Exception:
            logger.exception("Failed to create backup from remote flows for %s", config.name)
            return False

    def get_poll_interval(self, config):
        """Return the effective poll interval, with backoff on failures."""
        with self._lock:
            if self._auth_failure:
                return AUTH_BACKOFF_SECONDS
            base = config.poll_interval_seconds
            if self._consecutive_failures >= BACKOFF_THRESHOLD:
                return min(base * (2 ** (self._consecutive_failures - BACKOFF_THRESHOLD + 1)), MAX_BACKOFF_SECONDS)
            return base


def _run_remote_polling_loop(poller, stop_event, config_id):
    """Background thread that polls a remote Node-RED instance."""
    from backup.models import NodeRedConfig

    logger.info("Remote poller started for config %d", config_id)

    while not stop_event.is_set():
        try:
            config = NodeRedConfig.objects.get(pk=config_id)
            interval = poller.get_poll_interval(config)
        except NodeRedConfig.DoesNotExist:
            logger.warning("Remote poller stopping — config %d no longer exists", config_id)
            break

        if stop_event.wait(timeout=interval):
            break

        if not stop_event.is_set():
            try:
                poller.poll_once()
            except Exception:
                logger.exception("Error during remote poll for config %d", config_id)

    logger.info("Remote poller stopped for config %d", config_id)


def start_all_remote_pollers(stop_event):
    """Start remote pollers for all enabled remote instances.

    Returns:
        List of polling threads (already started).
    """
    from backup.models import NodeRedConfig

    configs = list(
        NodeRedConfig.objects.filter(
            is_enabled=True, source_type="remote", watch_enabled=True,
        )
    )
    threads = []

    for config in configs:
        poller = RemotePoller(config.pk)
        thread = threading.Thread(
            target=_run_remote_polling_loop,
            args=(poller, stop_event, config.pk),
            daemon=False,
        )
        thread.start()
        threads.append(thread)
        logger.info(
            "Remote poller started for %s (%s), interval=%ds",
            config.name, config.nodered_url, config.poll_interval_seconds,
        )

    return threads
