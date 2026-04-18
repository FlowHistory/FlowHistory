from django.db.models import Count, Sum
from prometheus_client.core import GaugeMetricFamily

from .models import BackupRecord, NodeRedConfig, RestoreRecord


class FlowHistoryCollector:
    """Emits per-instance FlowHistory domain metrics at scrape time.

    The DB is already the source of truth for these values (see dashboard
    aggregations in backup/views/pages.py), so the collector performs small
    indexed aggregations on each scrape rather than maintaining in-process
    counters that would need multi-process coordination.
    """

    def describe(self):
        # prometheus_client's REGISTRY.register() calls describe() once at
        # registration. The default Collector.describe() runs collect(), which
        # would hit the DB during app startup (e.g. collectstatic) before the
        # schema exists. Returning an empty iterable keeps registration cheap.
        return []

    def collect(self):
        configs = list(NodeRedConfig.objects.all())
        slugs = [c.slug for c in configs]

        yield from self._instance_state(configs)
        yield from self._backup_totals(slugs)
        yield from self._backup_bytes(slugs)
        yield from self._last_backup_age(configs)
        yield from self._pinned_totals(slugs)
        yield from self._restore_totals(slugs)

    def _instance_state(self, configs):
        enabled = GaugeMetricFamily(
            "flowhistory_instance_enabled",
            "Whether the instance is enabled (1) or disabled (0).",
            labels=["instance"],
        )
        errored = GaugeMetricFamily(
            "flowhistory_instance_has_error",
            "1 if the instance's most recent backup attempt recorded an error.",
            labels=["instance"],
        )
        for c in configs:
            enabled.add_metric([c.slug], 1 if c.is_enabled else 0)
            errored.add_metric([c.slug], 1 if c.last_backup_error else 0)
        yield enabled
        yield errored

    def _backup_totals(self, slugs):
        metric = GaugeMetricFamily(
            "flowhistory_backups",
            "Count of backup records per instance and status.",
            labels=["instance", "status"],
        )
        rows = (
            BackupRecord.objects.values("config__slug", "status")
            .annotate(count=Count("id"))
            .order_by()
        )
        seen = set()
        for row in rows:
            slug = row["config__slug"]
            status = row["status"]
            metric.add_metric([slug, status], row["count"])
            seen.add((slug, status))
        # Emit zeros for known (instance, status) pairs so absence reads as 0.
        for slug in slugs:
            for status in ("success", "failed"):
                if (slug, status) not in seen:
                    metric.add_metric([slug, status], 0)
        yield metric

    def _backup_bytes(self, slugs):
        metric = GaugeMetricFamily(
            "flowhistory_backup_bytes",
            "Total size in bytes of successful backups per instance.",
            labels=["instance"],
        )
        rows = (
            BackupRecord.objects.filter(status="success")
            .values("config__slug")
            .annotate(total=Sum("file_size"))
            .order_by()
        )
        seen = {row["config__slug"]: row["total"] or 0 for row in rows}
        for slug in slugs:
            metric.add_metric([slug], seen.get(slug, 0))
        yield metric

    def _last_backup_age(self, configs):
        metric = GaugeMetricFamily(
            "flowhistory_last_successful_backup_timestamp_seconds",
            "Unix timestamp of the last successful backup per instance. "
            "0 if no successful backup has been recorded.",
            labels=["instance"],
        )
        for c in configs:
            ts = c.last_successful_backup.timestamp() if c.last_successful_backup else 0
            metric.add_metric([c.slug], ts)
        yield metric

    def _pinned_totals(self, slugs):
        metric = GaugeMetricFamily(
            "flowhistory_pinned_backups",
            "Count of pinned backups per instance.",
            labels=["instance"],
        )
        rows = (
            BackupRecord.objects.filter(is_pinned=True)
            .values("config__slug")
            .annotate(count=Count("id"))
            .order_by()
        )
        seen = {row["config__slug"]: row["count"] for row in rows}
        for slug in slugs:
            metric.add_metric([slug], seen.get(slug, 0))
        yield metric

    def _restore_totals(self, slugs):
        metric = GaugeMetricFamily(
            "flowhistory_restores",
            "Count of restore attempts per instance and status.",
            labels=["instance", "status"],
        )
        rows = (
            RestoreRecord.objects.values("config__slug", "status")
            .annotate(count=Count("id"))
            .order_by()
        )
        seen = set()
        for row in rows:
            slug = row["config__slug"]
            status = row["status"]
            metric.add_metric([slug, status], row["count"])
            seen.add((slug, status))
        for slug in slugs:
            for status in ("success", "failed"):
                if (slug, status) not in seen:
                    metric.add_metric([slug, status], 0)
        yield metric
