"""Move backup archives from root backups/ into per-instance backups/<slug>/ dirs."""

import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from backup.models import BackupRecord


class Command(BaseCommand):
    help = "Migrate backup archives into per-instance subdirectories"

    def handle(self, *args, **options):
        backup_root = Path(settings.BACKUP_DIR)

        # Find .tar.gz files directly in the root (not in subdirectories)
        root_archives = [
            f for f in backup_root.iterdir()
            if f.is_file() and f.name.endswith(".tar.gz")
        ]

        if not root_archives:
            self.stdout.write("No archives to migrate (root backups/ is clean)")
            return

        moved = 0
        orphaned = 0

        for archive in root_archives:
            record = BackupRecord.objects.filter(filename=archive.name).first()

            if record and record.config:
                # Move to per-instance directory
                dest_dir = record.config.backup_dir
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / archive.name
                shutil.move(str(archive), str(dest))
                record.file_path = str(dest)
                record.save(update_fields=["file_path"])
                moved += 1
            else:
                # No matching record — move to _orphaned/
                orphan_dir = backup_root / "_orphaned"
                orphan_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(archive), str(orphan_dir / archive.name))
                orphaned += 1

        self.stdout.write(
            f"Storage migration: {moved} moved, {orphaned} orphaned"
        )
