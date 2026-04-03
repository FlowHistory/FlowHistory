import os

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from .models import BackupRecord, NodeRedConfig


def _get_or_create_config():
    config, _ = NodeRedConfig.objects.get_or_create(pk=1)
    return config


@require_GET
def dashboard(request):
    config = _get_or_create_config()
    backups = BackupRecord.objects.filter(config=config)
    flows_path = config.flows_path
    flows_accessible = os.path.isfile(flows_path)
    last_backup = backups.first()

    return render(request, "backup/dashboard.html", {
        "config": config,
        "backups": backups[:50],
        "backup_count": backups.count(),
        "flows_accessible": flows_accessible,
        "last_backup": last_backup,
    })


@require_GET
def health_check(request):
    return JsonResponse({"status": "ok"})


@require_POST
def api_create_backup(request):
    return JsonResponse(
        {"status": "error", "message": "Backup service not yet implemented"},
        status=501,
    )
