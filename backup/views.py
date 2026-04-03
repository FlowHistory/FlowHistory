import json
import logging
import os
import tarfile
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from .forms import NodeRedConfigForm
from .models import BackupRecord, NodeRedConfig, RestoreRecord
from .services.backup_service import create_backup
from .services.diff_service import diff_backup_archives
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
    try:
        backup = BackupRecord.objects.get(pk=backup_id, config=config)
    except BackupRecord.DoesNotExist:
        messages.error(request, "Backup not found.")
        return redirect("dashboard")

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
    try:
        backup = BackupRecord.objects.get(pk=backup_id, config=config, status="success")
    except BackupRecord.DoesNotExist:
        messages.error(request, "Backup not found.")
        return redirect("dashboard")
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


@require_POST
def api_set_label(request, backup_id):
    config = _get_or_create_config()
    try:
        backup = BackupRecord.objects.get(pk=backup_id, config=config)
    except BackupRecord.DoesNotExist:
        return JsonResponse(
            {"status": "error", "message": "Backup not found"}, status=404
        )
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse(
            {"status": "error", "message": "Invalid JSON body"}, status=400
        )
    if "label" not in body:
        return JsonResponse(
            {"status": "error", "message": "Missing 'label' field"}, status=400
        )
    label = body["label"]
    if not isinstance(label, str):
        return JsonResponse(
            {"status": "error", "message": "'label' must be a string"}, status=400
        )
    if len(label) > 200:
        return JsonResponse(
            {"status": "error", "message": "Label must be 200 characters or fewer"},
            status=400,
        )
    backup.label = label
    backup.save(update_fields=["label"])
    return JsonResponse({
        "status": "success",
        "backup": {"id": backup.pk, "label": backup.label},
    })


@require_POST
def backup_delete(request, backup_id):
    config = _get_or_create_config()
    try:
        backup = BackupRecord.objects.get(pk=backup_id, config=config)
    except BackupRecord.DoesNotExist:
        messages.error(request, "Backup not found.")
        return redirect("dashboard")
    archive = Path(backup.file_path)
    if archive.is_file():
        archive.unlink()
    backup.delete()
    messages.success(request, "Backup deleted.")
    return redirect("dashboard")


def _classify_diff_lines(diff_text):
    """Classify each line of a unified diff for template rendering."""
    lines = []
    for line in diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            lines.append({"text": line, "type": "header"})
        elif line.startswith("@@"):
            lines.append({"text": line, "type": "header"})
        elif line.startswith("+"):
            lines.append({"text": line, "type": "add"})
        elif line.startswith("-"):
            lines.append({"text": line, "type": "remove"})
        else:
            lines.append({"text": line, "type": "context"})
    return lines


@require_GET
def diff_view(request, backup_id, compare_id=None):
    config = _get_or_create_config()
    try:
        backup_b = BackupRecord.objects.get(pk=backup_id, config=config, status="success")
    except BackupRecord.DoesNotExist:
        messages.error(request, "Backup not found.")
        return redirect("dashboard")

    diff_data = None
    archive_error = None

    if compare_id is not None:
        try:
            backup_a = BackupRecord.objects.get(pk=compare_id, config=config, status="success")
        except BackupRecord.DoesNotExist:
            messages.error(request, "Comparison backup not found.")
            return redirect("dashboard")
        # Ensure backup_a is older
        if backup_a.created_at > backup_b.created_at:
            backup_a, backup_b = backup_b, backup_a
        try:
            diff_data = diff_backup_archives(backup_a.file_path, backup_b.file_path)
        except (FileNotFoundError, tarfile.TarError) as exc:
            archive_error = str(exc)
    else:
        # Diff vs previous backup
        backup_a = (
            BackupRecord.objects
            .filter(config=config, status="success", created_at__lt=backup_b.created_at)
            .first()
        )
        if backup_a is not None:
            try:
                diff_data = diff_backup_archives(backup_a.file_path, backup_b.file_path)
            except (FileNotFoundError, tarfile.TarError):
                # Archives missing (e.g. retention cleanup) — use stored summary
                if backup_b.changes_summary:
                    diff_data = backup_b.changes_summary
                else:
                    archive_error = "Backup archives are no longer available on disk and no stored diff exists."

    # Ensure diff_data always has all keys (stored summaries may lack subflow keys)
    if diff_data:
        for key in ("subflows_added", "subflows_removed", "subflows_modified"):
            diff_data.setdefault(key, [])

    # Build tab overview from tab_summary fields
    tab_overview = []
    if diff_data and backup_a:
        tabs_a = set(backup_a.tab_summary or [])
        tabs_b = set(backup_b.tab_summary or [])
        added_labels = set(diff_data.get("tabs_added", []))
        removed_labels = set(diff_data.get("tabs_removed", []))
        modified_labels = {t["label"] for t in diff_data.get("tabs_modified", [])}
        changed_tabs = sorted(added_labels | removed_labels | modified_labels)
        for tab in changed_tabs:
            if tab in added_labels:
                tab_overview.append({"label": tab, "status": "added"})
            elif tab in removed_labels:
                tab_overview.append({"label": tab, "status": "removed"})
            else:
                tab_overview.append({"label": tab, "status": "modified"})

    # Classify diff lines for syntax-highlighted rendering
    if diff_data:
        for container_list in (diff_data.get("tabs_modified", []),
                               diff_data.get("subflows_modified", [])):
            for container in container_list:
                for node in container.get("nodes_modified", []):
                    for fd in node.get("field_diffs", []):
                        fd["diff_lines"] = _classify_diff_lines(fd.get("diff", ""))

    # Summary stats
    summary_stats = {"tabs_added": 0, "tabs_removed": 0, "tabs_modified": 0,
                     "subflows_added": 0, "subflows_removed": 0, "subflows_modified": 0,
                     "nodes_changed": 0}
    if diff_data:
        summary_stats["tabs_added"] = len(diff_data.get("tabs_added", []))
        summary_stats["tabs_removed"] = len(diff_data.get("tabs_removed", []))
        summary_stats["tabs_modified"] = len(diff_data.get("tabs_modified", []))
        summary_stats["subflows_added"] = len(diff_data.get("subflows_added", []))
        summary_stats["subflows_removed"] = len(diff_data.get("subflows_removed", []))
        summary_stats["subflows_modified"] = len(diff_data.get("subflows_modified", []))
        for container_list in (diff_data.get("tabs_modified", []),
                               diff_data.get("subflows_modified", [])):
            for container in container_list:
                summary_stats["nodes_changed"] += (
                    len(container.get("nodes_added", []))
                    + len(container.get("nodes_removed", []))
                    + len(container.get("nodes_modified", []))
                )

    # Other backups for comparison dropdown
    all_backups = (
        BackupRecord.objects
        .filter(config=config, status="success")
        .exclude(pk=backup_b.pk)
        .values_list("pk", "created_at", "label")[:50]
    )

    has_changes = bool(diff_data and any(
        diff_data.get(k) for k in (
            "tabs_added", "tabs_removed", "tabs_modified",
            "subflows_added", "subflows_removed", "subflows_modified",
        )
    ))

    return render(request, "backup/diff.html", {
        "backup_a": backup_a,
        "backup_b": backup_b,
        "diff_data": diff_data,
        "has_changes": has_changes,
        "archive_error": archive_error,
        "tab_overview": tab_overview,
        "summary_stats": summary_stats,
        "all_backups": all_backups,
        "breadcrumb_items": [
            {"label": "Dashboard", "url": "/"},
            {"label": "Diff"},
        ],
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


def custom_404(request, exception):
    path = request.path.strip("/")
    if path.startswith("backup"):
        messages.error(request, "Backup not found.")
    elif path.startswith("diff"):
        messages.error(request, "Diff not found.")
    else:
        messages.error(request, "Page not found.")
    return redirect("dashboard")


def custom_500(request):
    messages.error(request, "An unexpected error occurred.")
    return redirect("dashboard")
