import json
import logging
from pathlib import Path

from django.http import JsonResponse
from django.views.decorators.http import require_POST

from ..models import BackupRecord
from ..services.backup_service import create_backup
from ..services.restore_service import restore_backup
from .pages import _get_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# API endpoints (instance-scoped)
# ---------------------------------------------------------------------------


@require_POST
def api_create_backup(request, slug):
    config = _get_config(slug)
    try:
        flows_data = None
        if config.source_type == "remote":
            from ..services.remote_service import fetch_remote_flows

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
                msg = body.get(
                    "error_description",
                    body.get("error", f"{e.response.status_code} {e.response.reason}"),
                )
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

    return JsonResponse(
        {
            "status": "success",
            "backup": {
                "id": record.pk,
                "filename": record.filename,
                "file_size": record.file_size,
                "checksum": record.checksum,
                "trigger": record.trigger,
                "created_at": record.created_at.isoformat(),
            },
        }
    )


@require_POST
def api_restore_backup(request, slug, backup_id):
    config = _get_config(slug)
    try:
        BackupRecord.objects.get(pk=backup_id, config=config)
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

    return JsonResponse(
        {
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
        }
    )


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
    return JsonResponse(
        {
            "status": "success",
            "backup": {"id": backup.pk, "label": backup.label},
        }
    )


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
    return JsonResponse(
        {
            "status": "success",
            "backup": {"id": backup.pk, "notes": backup.notes},
        }
    )


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
    return JsonResponse(
        {
            "status": "success",
            "backup": {"id": backup.pk, "is_pinned": backup.is_pinned},
        }
    )


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

    return JsonResponse(
        {
            "status": "success",
            "action": action,
            "affected": affected,
            "errors": errors,
        }
    )


@require_POST
def api_clear_error(request, slug):
    """Clear the last_backup_error for an instance."""
    config = _get_config(slug)
    config.last_backup_error = ""
    config.save(update_fields=["last_backup_error"])
    return JsonResponse({"status": "success"})


@require_POST
def api_test_notification(request, slug):
    """Send a test notification to all configured backends for this instance."""
    from ..services.notification_service import get_configured_backends_objects
    from ..services.notifications.base import NotificationPayload, NotifyEvent

    config = _get_config(slug)
    backends = get_configured_backends_objects(config)
    if not backends:
        return JsonResponse(
            {
                "status": "error",
                "message": "No notification backends configured for this instance",
            },
            status=400,
        )

    payload = NotificationPayload(
        event=NotifyEvent.BACKUP_SUCCESS,
        instance_name=config.name,
        instance_slug=config.slug,
        instance_color=config.color,
        title=f"Test notification \u2014 {config.name}",
        message="This is a test notification from FlowHistory.",
        trigger="test",
    )

    errors = []
    sent = []
    for backend in backends:
        try:
            backend.send(config, payload)
            sent.append(backend.name())
        except Exception as e:
            errors.append(f"{backend.name()}: {e}")

    if errors and not sent:
        return JsonResponse({"status": "error", "errors": errors}, status=500)
    if errors:
        return JsonResponse({"status": "partial", "backends": sent, "errors": errors})
    return JsonResponse({"status": "success", "backends": sent})


@require_POST
def api_test_connection(request, slug):
    """Test connection to a remote Node-RED instance."""
    import requests as http_requests

    from ..services.remote_service import fetch_remote_flows

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
        return JsonResponse(
            {
                "status": "success",
                "message": f"Connected successfully. Found {flow_count} flow objects.",
            }
        )
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
