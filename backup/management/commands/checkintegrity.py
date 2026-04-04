"""Remove backup records whose archive files no longer exist on disk."""

from pathlib import Path

from django.core.management.base import BaseCommand

from backup.models import BackupRecord


class Command(BaseCommand):
    help = "Remove orphaned backup records (archive file missing from disk)"

    def handle(self, *args, **options):
        orphaned = [
            r
            for r in BackupRecord.objects.filter(status="success")
            if not Path(r.file_path).is_file()
        ]
        if not orphaned:
            self.stdout.write("Integrity check: all backup files present")
            return

        for record in orphaned:
            self.stderr.write(
                f"Removing orphaned record: {record.filename} "
                f"(missing {record.file_path})"
            )
            record.delete()

        self.stderr.write(f"Removed {len(orphaned)} orphaned backup record(s)")
