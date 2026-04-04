from django.db import models
from django.utils import timezone


class NodeRedConfig(models.Model):
    name = models.CharField(max_length=100, default="Node-RED")
    flows_path = models.CharField(max_length=500, default="/nodered-data/flows.json")
    backup_frequency = models.CharField(
        max_length=10,
        choices=[("hourly", "Hourly"), ("daily", "Daily"), ("weekly", "Weekly")],
        default="daily",
    )
    backup_time = models.TimeField(default="03:00")
    backup_day = models.SmallIntegerField(
        default=0, help_text="Day of week for weekly backups (0=Monday)"
    )
    max_backups = models.PositiveIntegerField(default=20)
    max_age_days = models.PositiveIntegerField(default=30)
    is_active = models.BooleanField(default=True)
    watch_enabled = models.BooleanField(default=True)
    watch_debounce_seconds = models.PositiveIntegerField(default=30)
    backup_credentials = models.BooleanField(default=True)
    backup_settings = models.BooleanField(default=False)
    restart_on_restore = models.BooleanField(default=False)
    nodered_container_name = models.CharField(max_length=100, default="nodered")
    last_successful_backup = models.DateTimeField(null=True, blank=True)
    last_backup_error = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "Node-RED Configuration"

    def __str__(self):
        return self.name


class BackupRecord(models.Model):
    TRIGGER_CHOICES = [
        ("manual", "Manual"),
        ("scheduled", "Scheduled"),
        ("file_change", "File Change"),
        ("pre_restore", "Pre-Restore Safety"),
    ]
    STATUS_CHOICES = [
        ("success", "Success"),
        ("failed", "Failed"),
    ]

    config = models.ForeignKey(
        NodeRedConfig, on_delete=models.CASCADE, related_name="backups"
    )
    created_at = models.DateTimeField(default=timezone.now)
    filename = models.CharField(max_length=255)
    file_path = models.CharField(max_length=500)
    file_size = models.BigIntegerField(default=0)
    checksum = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="success")
    error_message = models.TextField(blank=True, default="")
    trigger = models.CharField(max_length=20, choices=TRIGGER_CHOICES, default="manual")
    label = models.CharField(max_length=200, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    is_pinned = models.BooleanField(default=False)
    tab_summary = models.JSONField(default=list, blank=True)
    changes_summary = models.JSONField(default=dict, blank=True)
    includes_credentials = models.BooleanField(default=False)
    includes_settings = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.filename} ({self.get_trigger_display()})"


class RestoreRecord(models.Model):
    STATUS_CHOICES = [
        ("success", "Success"),
        ("failed", "Failed"),
    ]

    config = models.ForeignKey(
        NodeRedConfig, on_delete=models.CASCADE, related_name="restores"
    )
    backup = models.ForeignKey(
        BackupRecord, on_delete=models.SET_NULL, null=True, related_name="restores"
    )
    safety_backup = models.ForeignKey(
        BackupRecord,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="safety_for_restores",
    )
    created_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="success")
    error_message = models.TextField(blank=True, default="")
    container_restarted = models.BooleanField(default=False)
    restart_message = models.CharField(max_length=500, blank=True, default="")
    files_restored = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Restore from {self.backup} at {self.created_at}"
