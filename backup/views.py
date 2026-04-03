import logging
import os
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from .forms import NodeRedConfigForm
from .models import BackupRecord, NodeRedConfig, RestoreRecord
from .services.backup_service import create_backup
from .services.restore_service import restore_backup

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
def backup_detail(request, backup_id):
    config = _get_or_create_config()
    backup = get_object_or_404(BackupRecord, pk=backup_id, config=config)

    # Get previous backup for context
    prev_backup = (
        BackupRecord.objects
        .filter(config=config, status="success", created_at__lt=backup.created_at)
        .first()
    )

    # Get restore records for this backup
    restores = RestoreRecord.objects.filter(backup=backup)

    # Build includes string
    includes = ["flows.json"]
    if backup.includes_credentials:
        includes.append("credentials")
    if backup.includes_settings:
        includes.append("settings")

    return render(request, "backup/detail.html", {
        "config": config,
        "backup": backup,
        "prev_backup": prev_backup,
        "restores": restores,
        "backup_includes": ", ".join(includes),
        "breadcrumb_items": [
            {"label": "Dashboard", "url": "/"},
            {"label": "Backup Detail"},
        ],
    })


@require_GET
def backup_download(request, backup_id):
    config = _get_or_create_config()
    backup = get_object_or_404(BackupRecord, pk=backup_id, config=config, status="success")
    archive = Path(backup.file_path)
    if not archive.is_file():
        return JsonResponse({"status": "error", "message": "Archive not found"}, status=404)
    return FileResponse(open(archive, "rb"), as_attachment=True, filename=backup.filename)


def settings_view(request):
    config = _get_or_create_config()
    if request.method == "POST":
        form = NodeRedConfigForm(request.POST, instance=config)
        if form.is_valid():
            form.save()
            messages.success(request, "Settings saved successfully.")
            return redirect("settings")
    else:
        form = NodeRedConfigForm(instance=config)
    return render(request, "backup/settings.html", {"form": form})


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


@require_POST
def api_restore_backup(request, backup_id):
    try:
        record = restore_backup(backup_id)
    except BackupRecord.DoesNotExist:
        return JsonResponse(
            {"status": "error", "message": "Backup not found"},
            status=404,
        )
    except Exception:
        logger.exception("Unexpected error restoring backup %s", backup_id)
        return JsonResponse(
            {"status": "error", "message": "Internal error during restore"},
            status=500,
        )

    if record.status == "failed":
        return JsonResponse(
            {"status": "error", "message": record.error_message},
            status=500,
        )

    return JsonResponse({
        "status": "success",
        "restore": {
            "id": record.pk,
            "backup_id": record.backup_id,
            "safety_backup_id": record.safety_backup_id,
            "files_restored": record.files_restored,
            "container_restarted": record.container_restarted,
            "restart_message": record.restart_message,
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
