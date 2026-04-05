import json
import logging
import os
import tarfile
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from .models import BackupRecord, NodeRedConfig, RestoreRecord
from .services.backup_service import create_backup
from .services.diff_service import diff_backup_archives
from .services.restore_service import restore_backup

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
    from django.db.models import Count, Q, Sum

    configs = NodeRedConfig.objects.all()
    count = configs.count()
    if count == 1:
        return redirect("instance_dashboard", slug=configs.first().slug)
    if count == 0:
        return redirect("instance_add")

    annotated = configs.annotate(
        backup_count=Count("backups", filter=Q(backups__status="success")),
        total_size=Sum("backups__file_size", filter=Q(backups__status="success")),
    )

    instances = []
    for config in annotated:
        last_backup = (
            BackupRecord.objects
            .filter(config=config, status="success")
            .only("created_at")
            .first()
        )
        instances.append({
            "config": config,
            "backup_count": config.backup_count,
            "last_backup": last_backup,
            "total_size": config.total_size or 0,
            "is_healthy": not config.last_backup_error,
        })

    return render(request, "backup/dashboard.html", {
        "instances": instances,
        "total_instances": count,
    })


# ---------------------------------------------------------------------------
# Instance add (env-only — show instructions)
# ---------------------------------------------------------------------------


@require_GET
def instance_add(request):
    return render(request, "backup/instance_add.html", {
        "breadcrumb_items": [
            {"label": "Dashboard", "url": "/"},
            {"label": "Add Instance"},
        ],
    })


# ---------------------------------------------------------------------------
# Instance dashboard
# ---------------------------------------------------------------------------


@require_GET
def instance_dashboard(request, slug):
    config = _get_config(slug)
    backups = BackupRecord.objects.filter(config=config)
    flows_path = config.flows_path
    flows_accessible = os.path.isfile(flows_path) if config.source_type == "local" else None
    last_backup = backups.first()

    return render(request, "backup/instance_dashboard.html", {
        "config": config,
        "backups": backups[:50],
        "backup_count": backups.count(),
        "flows_accessible": flows_accessible,
        "last_backup": last_backup,
    })


# ---------------------------------------------------------------------------
# Instance settings
# ---------------------------------------------------------------------------


@require_GET
def instance_settings(request, slug):
    config = _get_config(slug)
    username, password = config.get_nodered_credentials()
    return render(request, "backup/settings.html", {
        "config": config,
        "has_credentials": bool(username),
        "breadcrumb_items": [
            {"label": "Dashboard", "url": "/"},
            {"label": config.name, "url": config.get_absolute_url()},
            {"label": "Settings"},
        ],
    })


# ---------------------------------------------------------------------------
# Instance delete
# ---------------------------------------------------------------------------


def instance_delete(request, slug):
    config = _get_config(slug)

    if request.method == "POST":
        name = config.name
        delete_files = request.POST.get("delete_files") == "on"

        if delete_files:
            import shutil
            backup_dir = config.backup_dir
            if backup_dir.is_dir():
                shutil.rmtree(backup_dir, ignore_errors=True)

        config.delete()
        messages.success(request, f'Instance "{name}" deleted.')
        return redirect("dashboard")

    # GET — show confirmation page
    backups = BackupRecord.objects.filter(config=config, status="success")
    total_size = sum(b.file_size for b in backups.only("file_size"))

    return render(request, "backup/instance_delete.html", {
        "config": config,
        "backup_count": backups.count(),
        "total_size": total_size,
        "breadcrumb_items": [
            {"label": "Dashboard", "url": "/"},
            {"label": config.name, "url": config.get_absolute_url()},
            {"label": "Delete"},
        ],
    })


# ---------------------------------------------------------------------------
# Backup detail / download / delete
# ---------------------------------------------------------------------------


@require_GET
def backup_detail(request, slug, backup_id):
    config = _get_config(slug)
    try:
        backup = BackupRecord.objects.get(pk=backup_id, config=config)
    except BackupRecord.DoesNotExist:
        messages.error(request, "Backup not found.")
        return redirect("instance_dashboard", slug=slug)

    prev_backup = (
        BackupRecord.objects
        .filter(config=config, status="success", created_at__lt=backup.created_at)
        .first()
    )
    restores = RestoreRecord.objects.filter(backup=backup)

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
            {"label": config.name, "url": config.get_absolute_url()},
            {"label": "Backup Detail"},
        ],
    })


@require_GET
def backup_download(request, slug, backup_id):
    config = _get_config(slug)
    try:
        backup = BackupRecord.objects.get(pk=backup_id, config=config, status="success")
    except BackupRecord.DoesNotExist:
        messages.error(request, "Backup not found.")
        return redirect("instance_dashboard", slug=slug)
    archive = Path(backup.file_path)
    if not archive.is_file():
        return JsonResponse({"status": "error", "message": "Archive not found"}, status=404)
    return FileResponse(open(archive, "rb"), as_attachment=True, filename=backup.filename)


@require_POST
def backup_delete(request, slug, backup_id):
    config = _get_config(slug)
    try:
        backup = BackupRecord.objects.get(pk=backup_id, config=config)
    except BackupRecord.DoesNotExist:
        messages.error(request, "Backup not found.")
        return redirect("instance_dashboard", slug=slug)
    archive = Path(backup.file_path)
    if archive.is_file():
        archive.unlink()
    backup.delete()
    messages.success(request, "Backup deleted.")
    return redirect("instance_dashboard", slug=slug)


# ---------------------------------------------------------------------------
# Diff view
# ---------------------------------------------------------------------------


def _classify_diff_lines(diff_text):
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
def diff_view(request, slug, backup_id, compare_id=None):
    config = _get_config(slug)
    try:
        backup_b = BackupRecord.objects.get(pk=backup_id, config=config, status="success")
    except BackupRecord.DoesNotExist:
        messages.error(request, "Backup not found.")
        return redirect("instance_dashboard", slug=slug)

    diff_data = None
    archive_error = None

    if compare_id is not None:
        try:
            backup_a = BackupRecord.objects.get(pk=compare_id, config=config, status="success")
        except BackupRecord.DoesNotExist:
            messages.error(request, "Comparison backup not found.")
            return redirect("instance_dashboard", slug=slug)
        if backup_a.created_at > backup_b.created_at:
            backup_a, backup_b = backup_b, backup_a
        try:
            diff_data = diff_backup_archives(backup_a.file_path, backup_b.file_path)
        except (FileNotFoundError, tarfile.TarError) as exc:
            archive_error = str(exc)
    else:
        backup_a = (
            BackupRecord.objects
            .filter(config=config, status="success", created_at__lt=backup_b.created_at)
            .first()
        )
        if backup_a is not None:
            try:
                diff_data = diff_backup_archives(backup_a.file_path, backup_b.file_path)
            except (FileNotFoundError, tarfile.TarError):
                if backup_b.changes_summary:
                    diff_data = backup_b.changes_summary
                else:
                    archive_error = "Backup archives are no longer available on disk and no stored diff exists."

    if diff_data:
        for key in ("subflows_added", "subflows_removed", "subflows_modified"):
            diff_data.setdefault(key, [])

    tab_overview = []
    if diff_data and backup_a:
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

    if diff_data:
        for container_list in (diff_data.get("tabs_modified", []),
                               diff_data.get("subflows_modified", [])):
            for container in container_list:
                for node in container.get("nodes_modified", []):
                    for fd in node.get("field_diffs", []):
                        fd["diff_lines"] = _classify_diff_lines(fd.get("diff", ""))

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
        "config": config,
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
            {"label": config.name, "url": config.get_absolute_url()},
            {"label": "Diff"},
        ],
    })


# ---------------------------------------------------------------------------
# API endpoints (instance-scoped)
# ---------------------------------------------------------------------------


@require_POST
def api_create_backup(request, slug):
    config = _get_config(slug)
    try:
        flows_data = None
        if config.source_type == "remote":
            from .services.remote_service import fetch_remote_flows
            flows_data, _ = fetch_remote_flows(config)
        record = create_backup(config=config, trigger="manual", flows_data=flows_data)
    except Exception as e:
        logger.exception("Unexpected error creating backup")
        import requests as http_requests
        if isinstance(e, http_requests.ConnectionError):
            msg = f"Cannot connect to {config.nodered_url}"
        elif isinstance(e, http_requests.HTTPError) and e.response is not None:
            try:
                body = e.response.json()
                msg = body.get("error_description", body.get("error", f"{e.response.status_code} {e.response.reason}"))
            except Exception:
                msg = f"{e.response.status_code} {e.response.reason}"
        else:
            msg = str(e)
        config.last_backup_error = msg
        config.save(update_fields=["last_backup_error"])
        return JsonResponse(
            {"status": "error", "message": msg},
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
def api_restore_backup(request, slug, backup_id):
    config = _get_config(slug)
    try:
        backup = BackupRecord.objects.get(pk=backup_id, config=config)
    except BackupRecord.DoesNotExist:
        return JsonResponse(
            {"status": "error", "message": "Backup not found"}, status=404
        )
    try:
        record = restore_backup(backup_id)
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
def api_set_label(request, slug, backup_id):
    config = _get_config(slug)
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
def api_set_notes(request, slug, backup_id):
    config = _get_config(slug)
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
    if "notes" not in body:
        return JsonResponse(
            {"status": "error", "message": "Missing 'notes' field"}, status=400
        )
    notes = body["notes"]
    if not isinstance(notes, str):
        return JsonResponse(
            {"status": "error", "message": "'notes' must be a string"}, status=400
        )
    backup.notes = notes
    backup.save(update_fields=["notes"])
    return JsonResponse({
        "status": "success",
        "backup": {"id": backup.pk, "notes": backup.notes},
    })


@require_POST
def api_toggle_pin(request, slug, backup_id):
    config = _get_config(slug)
    try:
        backup = BackupRecord.objects.get(pk=backup_id, config=config)
    except BackupRecord.DoesNotExist:
        return JsonResponse(
            {"status": "error", "message": "Backup not found"}, status=404
        )
    backup.is_pinned = not backup.is_pinned
    backup.save(update_fields=["is_pinned"])
    return JsonResponse({
        "status": "success",
        "backup": {"id": backup.pk, "is_pinned": backup.is_pinned},
    })


@require_POST
def api_bulk_action(request, slug):
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse(
            {"status": "error", "message": "Invalid JSON body"}, status=400
        )
    ids = body.get("ids", [])
    action = body.get("action")

    if action not in ("delete", "pin", "unpin"):
        return JsonResponse(
            {"status": "error", "message": "Invalid action"}, status=400
        )
    if not ids or not isinstance(ids, list) or len(ids) > 100:
        return JsonResponse(
            {"status": "error", "message": "Select 1-100 backups"}, status=400
        )

    config = _get_config(slug)
    backups = BackupRecord.objects.filter(pk__in=ids, config=config)
    found_ids = set(backups.values_list("pk", flat=True))
    errors = [f"Backup {mid} not found" for mid in ids if mid not in found_ids]
    affected = 0

    if action in ("pin", "unpin"):
        affected = backups.update(is_pinned=(action == "pin"))
    else:
        for backup in backups:
            try:
                Path(backup.file_path).unlink(missing_ok=True)
                backup.delete()
                affected += 1
            except Exception as e:
                errors.append(f"Backup {backup.pk}: {e}")

    return JsonResponse({
        "status": "success",
        "action": action,
        "affected": affected,
        "errors": errors,
    })


@require_POST
def api_test_connection(request, slug):
    """Test connection to a remote Node-RED instance."""
    import requests as http_requests

    from .services.remote_service import fetch_remote_flows

    config = _get_config(slug)
    if config.source_type != "remote":
        return JsonResponse(
            {"status": "error", "message": "Not a remote instance"}, status=400
        )
    if not config.nodered_url:
        return JsonResponse(
            {"status": "error", "message": "No URL configured"}, status=400
        )

    try:
        flows_text, _ = fetch_remote_flows(config)
        import json
        flows = json.loads(flows_text)
        flow_count = len(flows) if isinstance(flows, list) else 0
        return JsonResponse({
            "status": "success",
            "message": f"Connected successfully. Found {flow_count} flow objects.",
        })
    except http_requests.ConnectionError:
        return JsonResponse(
            {"status": "error", "message": f"Cannot connect to {config.nodered_url}"},
            status=502,
        )
    except http_requests.Timeout:
        return JsonResponse(
            {"status": "error", "message": "Connection timed out"},
            status=504,
        )
    except Exception as e:
        return JsonResponse(
            {"status": "error", "message": str(e)},
            status=500,
        )


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@require_GET
def health_check(request):
    return JsonResponse({"status": "ok"})


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
    messages.error(request, "Page not found.")
    return redirect("dashboard")


def custom_500(request):
    messages.error(request, "An unexpected error occurred.")
    return redirect("dashboard")
