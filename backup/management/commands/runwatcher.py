"""Django management command to run file watchers and remote pollers."""

import signal
import threading

from django.core.management.base import BaseCommand

from backup.services.remote_service import start_all_remote_pollers
from backup.services.watcher_service import start_all_watchers


class Command(BaseCommand):
    help = (
        "Start file watchers for local instances"
        " and remote pollers for remote instances"
    )

    def handle(self, *args, **options):
        stop_event = threading.Event()

        # Start remote pollers in background threads
        remote_threads = start_all_remote_pollers(stop_event)
        if remote_threads:
            self.stdout.write(f"Started {len(remote_threads)} remote poller(s)")

        # start_all_watchers blocks until shutdown signal.
        # If it exits (no local instances), we still need to wait for remote pollers.
        from backup.models import NodeRedConfig

        has_local = NodeRedConfig.objects.filter(
            is_enabled=True,
            source_type="local",
            watch_enabled=True,
        ).exists()

        if has_local:
            # This blocks until SIGTERM/SIGINT
            start_all_watchers()
            # Signal remote pollers to stop too
            stop_event.set()
        elif remote_threads:
            # No local instances, but remote pollers running — block until signal
            self.stdout.write("No local instances, waiting for remote pollers...")

            def _shutdown(signum, frame):
                stop_event.set()

            signal.signal(signal.SIGTERM, _shutdown)
            signal.signal(signal.SIGINT, _shutdown)
            stop_event.wait()
        else:
            self.stdout.write("No enabled instances found, watcher idle.")

        # Wait for remote threads to finish
        for t in remote_threads:
            t.join(timeout=5)
