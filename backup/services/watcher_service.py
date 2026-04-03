"""File watcher for Node-RED flows.json changes with debouncing."""

import logging
import signal
import threading
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


class _FlowsHandler(FileSystemEventHandler):
    """Watchdog handler that debounces flows.json modification events."""

    def __init__(self, flows_filename):
        super().__init__()
        self._flows_filename = flows_filename
        self._timer = None
        self._lock = threading.Lock()

    def on_modified(self, event):
        if event.is_directory:
            return

        # Only react to flows.json modifications
        if Path(event.src_path).name != self._flows_filename:
            return

        # Re-read config for current debounce setting and watch_enabled
        from backup.models import NodeRedConfig

        try:
            config = NodeRedConfig.objects.get(pk=1)
        except NodeRedConfig.DoesNotExist:
            return

        if not config.watch_enabled:
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
            "flows.json modified, debounce timer reset (%ds)", debounce_seconds
        )

    def _on_debounce_complete(self):
        """Called when the debounce timer fires (no changes for N seconds)."""
        from backup.models import NodeRedConfig
        from backup.services.backup_service import create_backup

        try:
            config = NodeRedConfig.objects.get(pk=1)
        except NodeRedConfig.DoesNotExist:
            logger.warning("No NodeRedConfig found, skipping file-change backup")
            return

        if not config.watch_enabled:
            logger.info("File-change backup skipped — watch_enabled is False")
            return

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


def start_watcher():
    """Start the file watcher. Blocks until SIGINT/SIGTERM.

    Reads flows_path from NodeRedConfig to determine which directory and
    file to watch.
    """
    from backup.models import NodeRedConfig

    config, _ = NodeRedConfig.objects.get_or_create(pk=1)
    flows_path = Path(config.flows_path)
    watch_dir = str(flows_path.parent)
    flows_filename = flows_path.name

    handler = _FlowsHandler(flows_filename)
    observer = Observer()
    observer.schedule(handler, watch_dir, recursive=False)

    # Graceful shutdown on signals
    stop_event = threading.Event()

    def _shutdown(signum, frame):
        logger.info("Received signal %s, stopping watcher", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    observer.start()
    logger.info("File watcher started on %s (watching %s)", watch_dir, flows_filename)

    try:
        stop_event.wait()
    finally:
        observer.stop()
        observer.join()
        logger.info("File watcher stopped")
