"""Django management command to run APScheduler for scheduled backups."""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from django.conf import settings
from django.core.management.base import BaseCommand
from django_apscheduler.jobstores import DjangoJobStore

from backup.models import NodeRedConfig
from backup.services.backup_service import create_backup
from backup.services.retention_service import apply_retention

logger = logging.getLogger(__name__)


def _scheduled_backup():
    """Job function: create a scheduled backup."""
    try:
        config, _ = NodeRedConfig.objects.get_or_create(pk=1)
        if not config.is_active:
            logger.info("Scheduled backup skipped — is_active is False")
            return
        result = create_backup(config=config, trigger="scheduled")
        if result is None:
            logger.info("Scheduled backup skipped — no changes")
        elif result.status == "success":
            logger.info("Scheduled backup created: %s", result.filename)
        else:
            logger.error("Scheduled backup failed: %s", result.error_message)
    except Exception:
        logger.exception("Unexpected error in scheduled backup")


def _scheduled_retention():
    """Job function: run retention cleanup."""
    try:
        result = apply_retention()
        logger.info(
            "Retention cleanup: %d by count, %d by age",
            result["deleted_by_count"],
            result["deleted_by_age"],
        )
    except Exception:
        logger.exception("Unexpected error in scheduled retention")


class Command(BaseCommand):
    help = "Start APScheduler for scheduled backups and retention"

    def handle(self, *args, **options):
        scheduler = BlockingScheduler(timezone=settings.TIME_ZONE)
        scheduler.add_jobstore(DjangoJobStore(), "default")

        config, _ = NodeRedConfig.objects.get_or_create(pk=1)

        # Backup job with schedule from config
        trigger = self._build_trigger(config)
        scheduler.add_job(
            _scheduled_backup,
            trigger=trigger,
            id="scheduled_backup",
            replace_existing=True,
            max_instances=1,
        )

        # Retention job runs daily at 04:00 as a safety net
        scheduler.add_job(
            _scheduled_retention,
            trigger=CronTrigger(hour=4, minute=0),
            id="scheduled_retention",
            replace_existing=True,
            max_instances=1,
        )

        self.stdout.write("Starting scheduler...")
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            self.stdout.write("Scheduler stopped.")

    @staticmethod
    def _build_trigger(config):
        """Build a CronTrigger from NodeRedConfig settings."""
        hour = config.backup_time.hour
        minute = config.backup_time.minute

        if config.backup_frequency == "hourly":
            return CronTrigger(minute=minute)
        elif config.backup_frequency == "weekly":
            return CronTrigger(
                day_of_week=str(config.backup_day),
                hour=hour,
                minute=minute,
            )
        else:  # daily (default)
            return CronTrigger(hour=hour, minute=minute)
