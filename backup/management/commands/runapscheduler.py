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


def _scheduled_backup(config_id):
    """Job function: create a scheduled backup for a specific instance."""
    try:
        config = NodeRedConfig.objects.get(pk=config_id)
        if not config.schedule_enabled:
            logger.info("Scheduled backup skipped for %s — schedule_enabled is False", config.name)
            return

        flows_data = None
        if config.source_type == "remote":
            from backup.services.remote_service import fetch_remote_flows
            flows_data, _ = fetch_remote_flows(config)

        result = create_backup(config=config, trigger="scheduled", flows_data=flows_data)
        if result is None:
            logger.info("Scheduled backup skipped for %s — no changes", config.name)
        elif result.status == "success":
            logger.info("Scheduled backup created for %s: %s", config.name, result.filename)
        else:
            logger.error("Scheduled backup failed for %s: %s", config.name, result.error_message)
    except NodeRedConfig.DoesNotExist:
        logger.warning("Scheduled backup skipped — config %d no longer exists", config_id)
    except Exception:
        logger.exception("Unexpected error in scheduled backup for config %d", config_id)


def _scheduled_retention(config_id):
    """Job function: run retention cleanup for a specific instance."""
    try:
        config = NodeRedConfig.objects.get(pk=config_id)
        result = apply_retention(config)
        logger.info(
            "Retention cleanup for %s: %d by count, %d by age",
            config.name,
            result["deleted_by_count"],
            result["deleted_by_age"],
        )
    except NodeRedConfig.DoesNotExist:
        logger.warning("Retention skipped — config %d no longer exists", config_id)
    except Exception:
        logger.exception("Unexpected error in scheduled retention for config %d", config_id)


class Command(BaseCommand):
    help = "Start APScheduler for scheduled backups and retention"

    def handle(self, *args, **options):
        scheduler = BlockingScheduler(timezone=settings.TIME_ZONE)
        scheduler.add_jobstore(DjangoJobStore(), "default")

        configs = NodeRedConfig.objects.filter(is_enabled=True)
        if not configs.exists():
            self.stdout.write("No enabled instances found, scheduler idle.")

        for config in configs:
            trigger = self._build_trigger(config)
            scheduler.add_job(
                _scheduled_backup,
                trigger=trigger,
                args=[config.pk],
                id=f"backup_{config.pk}",
                replace_existing=True,
                max_instances=1,
            )
            scheduler.add_job(
                _scheduled_retention,
                trigger=CronTrigger(hour=4, minute=0),
                args=[config.pk],
                id=f"retention_{config.pk}",
                replace_existing=True,
                max_instances=1,
            )
            self.stdout.write(f"Scheduled jobs for instance: {config.name} (pk={config.pk})")

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
