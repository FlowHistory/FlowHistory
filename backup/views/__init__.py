from .pages import (
    dashboard, instance_add, instance_dashboard,
    instance_settings, instance_delete,
)
from .backups import backup_detail, backup_download, backup_delete, diff_view
from .api import (
    api_create_backup, api_clear_error, api_restore_backup, api_set_label,
    api_set_notes, api_toggle_pin, api_bulk_action,
    api_test_notification, api_test_connection,
)
from .auth import health_check, login_view, logout_view, custom_404, custom_500
