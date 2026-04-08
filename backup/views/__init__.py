from .api import (
    api_bulk_action,
    api_clear_error,
    api_create_backup,
    api_import_backup,
    api_restore_backup,
    api_set_label,
    api_set_notes,
    api_test_connection,
    api_test_notification,
    api_toggle_pin,
)
from .auth import custom_404, custom_500, health_check, login_view, logout_view
from .backups import backup_delete, backup_detail, backup_download, diff_view
from .pages import (
    dashboard,
    instance_add,
    instance_dashboard,
    instance_delete,
    instance_settings,
)

__all__ = [
    "api_bulk_action",
    "api_clear_error",
    "api_create_backup",
    "api_import_backup",
    "api_restore_backup",
    "api_set_label",
    "api_set_notes",
    "api_test_connection",
    "api_test_notification",
    "api_toggle_pin",
    "backup_delete",
    "backup_detail",
    "backup_download",
    "custom_404",
    "custom_500",
    "dashboard",
    "diff_view",
    "health_check",
    "instance_add",
    "instance_dashboard",
    "instance_delete",
    "instance_settings",
    "login_view",
    "logout_view",
]
