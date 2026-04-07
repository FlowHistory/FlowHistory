from django.contrib import admin

from .models import BackupRecord, NodeRedConfig, RestoreRecord


@admin.register(NodeRedConfig)
class NodeRedConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "source_type", "is_enabled", "backup_frequency")
    list_filter = ("source_type", "is_enabled", "backup_frequency")
    search_fields = ("name", "slug", "env_prefix")
    readonly_fields = ("slug", "created_at")
    fieldsets = (
        (
            "Instance Identity",
            {
                "fields": ("name", "slug", "color", "is_enabled", "created_at"),
            },
        ),
        (
            "Source Configuration",
            {
                "fields": (
                    "source_type",
                    "nodered_url",
                    "env_prefix",
                    "flows_path",
                    "poll_interval_seconds",
                ),
            },
        ),
        (
            "Backup Schedule",
            {
                "fields": (
                    "backup_frequency",
                    "backup_time",
                    "backup_day",
                    "schedule_enabled",
                    "always_backup",
                ),
            },
        ),
        (
            "Retention Policy",
            {
                "fields": ("max_backups", "max_age_days"),
            },
        ),
        (
            "File Monitoring",
            {
                "fields": ("watch_enabled", "watch_debounce_seconds"),
            },
        ),
        (
            "Restore & Container",
            {
                "fields": (
                    "backup_credentials",
                    "backup_settings",
                    "restart_on_restore",
                    "nodered_container_name",
                ),
            },
        ),
        (
            "Status",
            {
                "fields": ("last_successful_backup", "last_backup_error"),
            },
        ),
    )


@admin.register(BackupRecord)
class BackupRecordAdmin(admin.ModelAdmin):
    list_display = ("filename", "config", "trigger", "status", "created_at")
    list_filter = ("status", "trigger", "config")
    search_fields = ("filename", "label")
    readonly_fields = ("created_at",)


@admin.register(RestoreRecord)
class RestoreRecordAdmin(admin.ModelAdmin):
    list_display = ("backup", "config", "status", "created_at")
    list_filter = ("status", "config")
    readonly_fields = ("created_at",)
