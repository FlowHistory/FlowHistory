import logging
import os

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from .models import BackupRecord, NodeRedConfig
from .services.backup_service import create_backup

logger = logging.getLogger(__name__)


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
    config = _get_or_create_config()
    try:
        record = create_backup(config=config, trigger="manual")
    except Exception:
        logger.exception("Unexpected error creating backup")
        return JsonResponse(
            {"status": "error", "message": "Internal error creating backup"},
            status=500,
        )

    if record is None:
        return JsonResponse(
            {"status": "skipped", "message": "Backup skipped — flows.json unchanged"},
            status=200,
        )

    if record.status == "failed":
        return JsonResponse(
            {"status": "error", "message": record.error_message},
            status=500,
        )

    return JsonResponse({
        "status": "success",
        "backup": {
            "id": record.pk,
            "filename": record.filename,
            "file_size": record.file_size,
            "checksum": record.checksum,
            "trigger": record.trigger,
            "created_at": record.created_at.isoformat(),
        },
    })


def login_view(request):
    if request.method == "POST":
        password = request.POST.get("password", "")
        if password == settings.APP_PASSWORD:
            request.session["authenticated"] = True
            return redirect("dashboard")
        return render(request, "backup/login.html", {"error": "Invalid password"})
    return render(request, "backup/login.html")


@require_POST
def logout_view(request):
    request.session.flush()
    return redirect("login")
