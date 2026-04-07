import tarfile
from pathlib import Path

from django.contrib import messages
from django.http import FileResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from ..models import BackupRecord, RestoreRecord
from ..services.diff_service import diff_backup_archives
from .pages import _get_config

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

    prev_backup = BackupRecord.objects.filter(
        config=config, status="success", created_at__lt=backup.created_at
    ).first()
    restores = RestoreRecord.objects.filter(backup=backup)

    includes = ["flows.json"]
    if backup.includes_credentials:
        includes.append("credentials")
    if backup.includes_settings:
        includes.append("settings")

    return render(
        request,
        "backup/detail.html",
        {
            "config": config,
            "backup": backup,
            "prev_backup": prev_backup,
            "restores": restores,
            "backup_includes": ", ".join(includes),
            "breadcrumb_items": [
                {"label": "Dashboard", "url": reverse("dashboard")},
                {"label": config.name, "url": config.get_absolute_url()},
                {"label": "Backup Detail"},
            ],
        },
    )


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
        return JsonResponse(
            {"status": "error", "message": "Archive not found"}, status=404
        )
    return FileResponse(
        open(archive, "rb"), as_attachment=True, filename=backup.filename
    )


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
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
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
        backup_b = BackupRecord.objects.get(
            pk=backup_id, config=config, status="success"
        )
    except BackupRecord.DoesNotExist:
        messages.error(request, "Backup not found.")
        return redirect("instance_dashboard", slug=slug)

    diff_data = None
    archive_error = None

    if compare_id is not None:
        try:
            backup_a = BackupRecord.objects.get(
                pk=compare_id, config=config, status="success"
            )
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
        backup_a = BackupRecord.objects.filter(
            config=config, status="success", created_at__lt=backup_b.created_at
        ).first()
        if backup_a is not None:
            try:
                diff_data = diff_backup_archives(backup_a.file_path, backup_b.file_path)
            except (FileNotFoundError, tarfile.TarError):
                if backup_b.changes_summary:
                    diff_data = backup_b.changes_summary
                else:
                    archive_error = (
                        "Backup archives are no longer available"
                        " on disk and no stored diff exists."
                    )

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
        for container_list in (
            diff_data.get("tabs_modified", []),
            diff_data.get("subflows_modified", []),
        ):
            for container in container_list:
                for node in container.get("nodes_modified", []):
                    for fd in node.get("field_diffs", []):
                        fd["diff_lines"] = _classify_diff_lines(fd.get("diff", ""))

    summary_stats = {
        "tabs_added": 0,
        "tabs_removed": 0,
        "tabs_modified": 0,
        "subflows_added": 0,
        "subflows_removed": 0,
        "subflows_modified": 0,
        "nodes_changed": 0,
    }
    if diff_data:
        summary_stats["tabs_added"] = len(diff_data.get("tabs_added", []))
        summary_stats["tabs_removed"] = len(diff_data.get("tabs_removed", []))
        summary_stats["tabs_modified"] = len(diff_data.get("tabs_modified", []))
        summary_stats["subflows_added"] = len(diff_data.get("subflows_added", []))
        summary_stats["subflows_removed"] = len(diff_data.get("subflows_removed", []))
        summary_stats["subflows_modified"] = len(diff_data.get("subflows_modified", []))
        for container_list in (
            diff_data.get("tabs_modified", []),
            diff_data.get("subflows_modified", []),
        ):
            for container in container_list:
                summary_stats["nodes_changed"] += (
                    len(container.get("nodes_added", []))
                    + len(container.get("nodes_removed", []))
                    + len(container.get("nodes_modified", []))
                )

    all_backups = (
        BackupRecord.objects.filter(config=config, status="success")
        .exclude(pk=backup_b.pk)
        .values_list("pk", "created_at", "label")[:50]
    )

    has_changes = bool(
        diff_data
        and any(
            diff_data.get(k)
            for k in (
                "tabs_added",
                "tabs_removed",
                "tabs_modified",
                "subflows_added",
                "subflows_removed",
                "subflows_modified",
            )
        )
    )

    return render(
        request,
        "backup/diff.html",
        {
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
                {"label": "Dashboard", "url": reverse("dashboard")},
                {"label": config.name, "url": config.get_absolute_url()},
                {"label": "Diff"},
            ],
        },
    )
