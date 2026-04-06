"""File watcher for Node-RED flows.json changes with debouncing.

Uses a hybrid approach: watchdog inotify events for instant detection,
plus periodic checksum polling as a fallback for Docker bind mount issues.
"""

import hashlib
import logging
import signal
import threading
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


class _FlowsHandler(FileSystemEventHandler):
    """Watchdog handler that debounces flows.json modification events."""

    def __init__(self, flows_path, config_id):
        super().__init__()
        self._flows_path = Path(flows_path)
        self._flows_filename = self._flows_path.name
        self._config_id = config_id
        self._timer = None
        self._lock = threading.Lock()
        self._last_known_checksum = self._compute_checksum()

    def _compute_checksum(self):
        """Compute SHA256 of flows.json, or None if file doesn't exist."""
        try:
            return hashlib.sha256(self._flows_path.read_bytes()).hexdigest()
        except OSError:
            return None

    def on_modified(self, event):
        self._handle_potential_change(event, event.src_path)

    def on_created(self, event):
        self._handle_potential_change(event, event.src_path)

    def on_moved(self, event):
        self._handle_potential_change(event, event.dest_path)

    def _handle_potential_change(self, event, path_to_check):
        """Shared handler for modified/created/moved events on flows.json."""
        logger.debug(
            "Filesystem event: type=%s src=%s dest=%s is_dir=%s",
            event.event_type,
            event.src_path,
            getattr(event, "dest_path", ""),
            event.is_directory,
        )

        if event.is_directory:
            logger.debug("Ignoring directory event")
            return

        if Path(path_to_check).name != self._flows_filename:
            logger.debug("Ignoring event for non-target file: %s", path_to_check)
            return

        logger.debug("flows.json %s detected via inotify", event.event_type)
        self._reset_debounce(source="inotify")

    def _reset_debounce(self, source="unknown"):
        """Reset the debounce timer. Called by both inotify and polling."""
        from backup.models import NodeRedConfig

        try:
            config = NodeRedConfig.objects.get(pk=self._config_id)
        except NodeRedConfig.DoesNotExist:
            logger.debug("Ignoring event — no NodeRedConfig found")
            return

        if not config.watch_enabled:
            logger.debug("Ignoring event — watch_enabled is False")
            return

        debounce_seconds = config.watch_debounce_seconds

        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(
                debounce_seconds, self._on_debounce_complete
            )
            self._timer.daemon = True
            self._timer.start()

        logger.debug(
            "Debounce timer reset (%ds) — source: %s", debounce_seconds, source
        )

    def _on_debounce_complete(self):
        """Called when the debounce timer fires (no changes for N seconds)."""
        from backup.models import NodeRedConfig
        from backup.services.backup_service import create_backup

        try:
            config = NodeRedConfig.objects.get(pk=self._config_id)
        except NodeRedConfig.DoesNotExist:
            logger.warning("No NodeRedConfig found, skipping file-change backup")
            return

        if not config.watch_enabled:
            logger.info("File-change backup skipped — watch_enabled is False")
            return

        # Update stored checksum after debounce completes
        self._last_known_checksum = self._compute_checksum()

        logger.info("Debounce complete, creating file-change backup")
        try:
            result = create_backup(config=config, trigger="file_change")
            if result is None:
                logger.info("File-change backup skipped — no changes (checksum match)")
            elif result.status == "success":
                logger.info("File-change backup created: %s", result.filename)
            else:
                logger.error("File-change backup failed: %s", result.error_message)
        except Exception:
            logger.exception("Unexpected error creating file-change backup")

    def poll_for_changes(self):
        """Check flows.json checksum; reset debounce if changed."""
        current = self._compute_checksum()
        if current is None:
            logger.debug("Poll: flows.json not found at %s", self._flows_path)
            return

        with self._lock:
            previous = self._last_known_checksum

        if current != previous:
            logger.info(
                "Poll detected flows.json change (inotify missed it) — "
                "checksum %s → %s",
                previous[:12] if previous else "None",
                current[:12],
            )
            with self._lock:
                self._last_known_checksum = current
            self._reset_debounce(source="polling")
        else:
            logger.debug("Poll: no change (checksum %s)", current[:12])


def _run_polling_loop(handler, stop_event, config_id):
    """Background thread that periodically polls flows.json for changes."""
    from backup.models import NodeRedConfig

    logger.info("Polling thread started")
    while not stop_event.is_set():
        try:
            config = NodeRedConfig.objects.get(pk=config_id)
            interval = config.watch_debounce_seconds
        except NodeRedConfig.DoesNotExist:
            interval = 30

        if stop_event.wait(timeout=interval):
            break  # Stop event was set

        if not stop_event.is_set():
            try:
                handler.poll_for_changes()
            except Exception:
                logger.exception("Error during poll cycle")

    logger.info("Polling thread stopped")


def start_all_watchers():
    """Start file watchers for all enabled local instances. Blocks until SIGINT/SIGTERM.

    Creates one Observer with a handler per local instance, plus a polling
    fallback thread per instance for reliable Docker bind mount detection.
    """
    from backup.models import NodeRedConfig

    configs = list(
        NodeRedConfig.objects.filter(
            is_enabled=True, source_type="local", watch_enabled=True,
        )
    )
    if not configs:
        logger.info("No enabled local instances found, watcher idle")
        # Block until signal so the process doesn't exit
        stop_event = threading.Event()
        signal.signal(signal.SIGTERM, lambda s, f: stop_event.set())
        signal.signal(signal.SIGINT, lambda s, f: stop_event.set())
        stop_event.wait()
        return

    observer = Observer()
    stop_event = threading.Event()
    poll_threads = []

    for config in configs:
        flows_path = Path(config.flows_path)
        watch_dir = str(flows_path.parent)

        handler = _FlowsHandler(flows_path, config.pk)
        observer.schedule(handler, watch_dir, recursive=False)

        poll_thread = threading.Thread(
            target=_run_polling_loop,
            args=(handler, stop_event, config.pk),
            daemon=True,
        )
        poll_threads.append(poll_thread)

        logger.info(
            "File watcher started for %s on %s (watching %s)",
            config.name, watch_dir, flows_path.name,
        )
        logger.info(
            "Watcher config [%s]: watch_enabled=%s, debounce=%ds, "
            "flows_exists=%s, initial_checksum=%s",
            config.name,
            config.watch_enabled,
            config.watch_debounce_seconds,
            flows_path.is_file(),
            handler._last_known_checksum[:12] if handler._last_known_checksum else "None",
        )

    def _shutdown(signum, frame):
        logger.info("Received signal %s, stopping watchers", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    observer.start()
    for t in poll_threads:
        t.start()

    try:
        stop_event.wait()
    finally:
        observer.stop()
        observer.join(timeout=10)
        for t in poll_threads:
            t.join(timeout=5)
        logger.info("All file watchers stopped")
