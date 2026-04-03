"""Django management command to run the file watcher for flows.json changes."""

from django.core.management.base import BaseCommand

from backup.services.watcher_service import start_watcher


class Command(BaseCommand):
    help = "Start the file watcher for flows.json changes"

    def handle(self, *args, **options):
        self.stdout.write("Starting file watcher...")
        start_watcher()
