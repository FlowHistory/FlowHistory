import os
from pathlib import Path

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


class NodeRedConfig(models.Model):
    SOURCE_TYPE_CHOICES = [
        ("local", "Local"),
        ("remote", "Remote"),
    ]

    INSTANCE_COLORS = [
        "#3B82F6", "#EF4444", "#10B981", "#F59E0B", "#8B5CF6", "#EC4899",
    ]

    RESERVED_SLUGS = {"add", "api"}

    # Instance identity
    name = models.CharField(max_length=100, default="Node-RED")
    slug = models.SlugField(max_length=100, unique=True)
    color = models.CharField(max_length=7, blank=True, default="")
    is_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Source type
    source_type = models.CharField(
        max_length=10, choices=SOURCE_TYPE_CHOICES, default="local",
    )
    nodered_url = models.URLField(blank=True, default="")
    env_prefix = models.CharField(max_length=50, blank=True, default="")
    poll_interval_seconds = models.PositiveIntegerField(default=60)

    # Existing per-instance config
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
    schedule_enabled = models.BooleanField(default=True)
    always_backup = models.BooleanField(default=False)
    watch_enabled = models.BooleanField(default=True)
    watch_debounce_seconds = models.PositiveIntegerField(default=3)
    backup_credentials = models.BooleanField(default=False)
    backup_settings = models.BooleanField(default=False)
    restart_on_restore = models.BooleanField(default=False)
    nodered_container_name = models.CharField(max_length=100, default="nodered")
    last_successful_backup = models.DateTimeField(null=True, blank=True)
    last_backup_error = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "Node-RED Configuration"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse("instance_dashboard", kwargs={"slug": self.slug})

    @property
    def backup_dir(self):
        """Per-instance backup storage directory."""
        return Path(settings.BACKUP_DIR) / self.slug

    def get_nodered_credentials(self):
        """Read credentials from environment variables using configured prefix."""
        if not self.env_prefix:
            return None, None
        prefix = self.env_prefix.upper()
        username = os.environ.get(f"FLOWHISTORY_{prefix}_USER", "")
        password = os.environ.get(f"FLOWHISTORY_{prefix}_PASS", "")
        return username, password

    def save(self, *args, **kwargs):
        """Auto-generate slug from name with uniqueness dedup."""
        if not self.slug:
            base = slugify(self.name) or "instance"
            if base in self.RESERVED_SLUGS:
                base = f"{base}-instance"
            slug, n = base, 1
            while NodeRedConfig.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                n += 1
                slug = f"{base}-{n}"
            self.slug = slug
        if not self.color:
            used = set(
                NodeRedConfig.objects.exclude(pk=self.pk).values_list("color", flat=True)
            )
            for c in self.INSTANCE_COLORS:
                if c not in used:
                    self.color = c
                    break
            else:
                # All colors used — fall back to modulo
                self.color = self.INSTANCE_COLORS[len(used) % len(self.INSTANCE_COLORS)]
        super().save(*args, **kwargs)


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
