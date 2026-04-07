import logging
import os
import shutil

from django.contrib import messages
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from ..models import BackupRecord, NodeRedConfig

logger = logging.getLogger(__name__)


def _get_config(slug):
    """Get NodeRedConfig by slug or 404."""
    return get_object_or_404(NodeRedConfig, slug=slug)


# ---------------------------------------------------------------------------
# Aggregate dashboard
# ---------------------------------------------------------------------------


@require_GET
def dashboard(request):
    """Aggregate dashboard. Auto-redirects if only 1 instance."""
    from django.db.models import Count, Max, Q, Sum

    configs = NodeRedConfig.objects.all()
    count = configs.count()
    if count == 1:
        return redirect("instance_dashboard", slug=configs.first().slug)
    if count == 0:
        return redirect("instance_add")

    annotated = configs.annotate(
        backup_count=Count("backups", filter=Q(backups__status="success")),
        total_size=Sum("backups__file_size", filter=Q(backups__status="success")),
        last_backup_at=Max("backups__created_at", filter=Q(backups__status="success")),
    )

    instances = []
    for config in annotated:
        instances.append(
            {
                "config": config,
                "backup_count": config.backup_count,
                "last_backup_at": config.last_backup_at,
                "total_size": config.total_size or 0,
                "is_healthy": not config.last_backup_error,
            }
        )

    return render(
        request,
        "backup/dashboard.html",
        {
            "instances": instances,
            "total_instances": count,
        },
    )


# ---------------------------------------------------------------------------
# Instance add (env-only — show instructions)
# ---------------------------------------------------------------------------


@require_GET
def instance_add(request):
    return render(
        request,
        "backup/instance_add.html",
        {
            "breadcrumb_items": [
                {"label": "Dashboard", "url": reverse("dashboard")},
                {"label": "Add Instance"},
            ],
        },
    )


# ---------------------------------------------------------------------------
# Instance dashboard
# ---------------------------------------------------------------------------


@require_GET
def instance_dashboard(request, slug):
    config = _get_config(slug)
    backups = BackupRecord.objects.filter(config=config)
    flows_path = config.flows_path
    flows_accessible = (
        os.path.isfile(flows_path) if config.source_type == "local" else None
    )
    last_backup = backups.first()

    return render(
        request,
        "backup/instance_dashboard.html",
        {
            "config": config,
            "backups": backups[:50],
            "backup_count": backups.count(),
            "flows_accessible": flows_accessible,
            "last_backup": last_backup,
        },
    )


# ---------------------------------------------------------------------------
# Instance settings
# ---------------------------------------------------------------------------


@require_GET
def instance_settings(request, slug):
    config = _get_config(slug)
    username, password = config.get_nodered_credentials()

    from ..services.notification_service import get_configured_backends

    notification_backends = get_configured_backends(config)

    # Check backend config sources (instance-specific vs global)
    prefix = config.env_prefix.upper() if config.env_prefix else ""

    def _check_backend(*env_fields):
        """Return (instance_configured, global_configured) for backend env fields."""
        inst = all(
            prefix and os.environ.get(f"FLOWHISTORY_{prefix}_{f}", "").strip()
            for f in env_fields
        )
        glob = all(
            os.environ.get(f"FLOWHISTORY_NOTIFY_{f}", "").strip() for f in env_fields
        )
        return inst, glob

    notify_backend_status = []
    _backends = [
        ("Discord", "Discord webhook URL", ("DISCORD_WEBHOOK_URL",)),
        ("Slack", "Slack incoming webhook URL", ("SLACK_WEBHOOK_URL",)),
        (
            "Telegram",
            "Telegram bot token and chat ID",
            ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
        ),
        ("Pushbullet", "Pushbullet API key", ("PUSHBULLET_API_KEY",)),
        (
            "Home Assistant",
            "Home Assistant URL and long-lived access token",
            ("HOMEASSISTANT_URL", "HOMEASSISTANT_TOKEN"),
        ),
    ]
    for label, description, env_fields in _backends:
        inst_vars = " and ".join(f"FLOWHISTORY_{prefix}_{f}" for f in env_fields)
        global_vars = " and ".join(f"FLOWHISTORY_NOTIFY_{f}" for f in env_fields)
        tooltip = (
            f"{description}. Set per-instance via {inst_vars},"
            f" or globally via {global_vars}."
        )
        inst, glob = _check_backend(*env_fields)
        notify_backend_status.append(
            {
                "label": label,
                "tooltip": tooltip,
                "is_instance": inst,
                "is_global": glob,
            }
        )

    return render(
        request,
        "backup/settings.html",
        {
            "config": config,
            "has_credentials": bool(username),
            "defaults": NodeRedConfig.get_field_defaults(),
            "notification_backends": notification_backends,
            "notify_backend_status": notify_backend_status,
            "has_any_notification_backend": bool(notification_backends),
            "breadcrumb_items": [
                {"label": "Dashboard", "url": reverse("dashboard")},
                {"label": config.name, "url": config.get_absolute_url()},
                {"label": "Settings"},
            ],
        },
    )


# ---------------------------------------------------------------------------
# Instance delete
# ---------------------------------------------------------------------------


def instance_delete(request, slug):
    config = _get_config(slug)

    if request.method == "POST":
        name = config.name
        delete_files = request.POST.get("delete_files") == "on"

        if delete_files:
            backup_dir = config.backup_dir
            if backup_dir.is_dir():
                shutil.rmtree(backup_dir, ignore_errors=True)

        # Remove orphaned APScheduler jobs before deleting the config
        try:
            from django_apscheduler.models import DjangoJob

            DjangoJob.objects.filter(
                id__in=[f"backup_{config.pk}", f"retention_{config.pk}"]
            ).delete()
        except Exception:
            logger.warning("Could not clean up scheduler jobs for %s", name)

        config.delete()
        messages.success(request, f'Instance "{name}" deleted.')
        return redirect("dashboard")

    # GET — show confirmation page
    backups = BackupRecord.objects.filter(config=config, status="success")
    total_size = backups.aggregate(total=Sum("file_size"))["total"] or 0

    return render(
        request,
        "backup/instance_delete.html",
        {
            "config": config,
            "backup_count": backups.count(),
            "total_size": total_size,
            "breadcrumb_items": [
                {"label": "Dashboard", "url": reverse("dashboard")},
                {"label": config.name, "url": config.get_absolute_url()},
                {"label": "Delete"},
            ],
        },
    )
