"""Remove backup records whose archive files no longer exist on disk."""

from pathlib import Path

from django.core.management.base import BaseCommand

from backup.models import BackupRecord


class Command(BaseCommand):
    help = "Remove orphaned backup records (archive file missing from disk)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Actually delete orphaned records (default is warn-only)",
        )

    def handle(self, *args, **options):
        orphaned = [
            r
            for r in BackupRecord.objects.select_related("config").filter(status="success")
            if not Path(r.file_path).is_file()
        ]
        if not orphaned:
            self.stdout.write("Integrity check: all backup files present")
            return

        for record in orphaned:
            self.stderr.write(
                f"Orphaned record: {record.filename} "
                f"[{record.config.name}] "
                f"(missing {record.file_path})"
            )

        if options["delete"]:
            for record in orphaned:
                record.delete()
            self.stderr.write(
                f"Deleted {len(orphaned)} orphaned backup record(s)"
            )
        else:
            self.stderr.write(
                f"Found {len(orphaned)} orphaned record(s). "
                f"Run with --delete to remove them."
            )
