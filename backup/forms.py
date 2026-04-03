from django import forms

from .models import NodeRedConfig


class NodeRedConfigForm(forms.ModelForm):
    class Meta:
        model = NodeRedConfig
        fields = [
            "name",
            "flows_path",
            "is_active",
            "backup_frequency",
            "backup_time",
            "backup_day",
            "watch_enabled",
            "watch_debounce_seconds",
            "backup_credentials",
            "backup_settings",
            "max_backups",
            "max_age_days",
            "restart_on_restore",
            "nodered_container_name",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "flows_path": forms.TextInput(attrs={"class": "form-control"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "backup_frequency": forms.Select(attrs={"class": "form-select"}),
            "backup_time": forms.TimeInput(attrs={"class": "form-control", "type": "time"}),
            "backup_day": forms.Select(
                choices=[
                    (0, "Monday"),
                    (1, "Tuesday"),
                    (2, "Wednesday"),
                    (3, "Thursday"),
                    (4, "Friday"),
                    (5, "Saturday"),
                    (6, "Sunday"),
                ],
                attrs={"class": "form-select"},
            ),
            "watch_enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "watch_debounce_seconds": forms.NumberInput(attrs={"class": "form-control", "min": "5"}),
            "backup_credentials": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "backup_settings": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "max_backups": forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
            "max_age_days": forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
            "restart_on_restore": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "nodered_container_name": forms.TextInput(attrs={"class": "form-control"}),
        }
        labels = {
            "name": "Instance Name",
            "flows_path": "Flows File Path",
            "is_active": "Enable Scheduled Backups",
            "backup_frequency": "Backup Frequency",
            "backup_time": "Backup Time",
            "backup_day": "Day of Week",
            "watch_enabled": "Enable File Watcher",
            "watch_debounce_seconds": "Debounce (seconds)",
            "backup_credentials": "Include flows_cred.json",
            "backup_settings": "Include settings.js",
            "max_backups": "Maximum Backups",
            "max_age_days": "Maximum Age (days)",
            "restart_on_restore": "Restart Node-RED After Restore",
            "nodered_container_name": "Container Name",
        }
        help_texts = {
            "flows_path": "Path to flows.json inside the container",
            "backup_time": "Time for daily/weekly scheduled backups",
            "backup_day": "Only used when frequency is Weekly",
            "watch_debounce_seconds": "Wait this many seconds after the last file change before backing up",
            "max_backups": "Oldest backups are deleted when this limit is exceeded",
            "max_age_days": "Backups older than this are deleted",
            "nodered_container_name": "Docker container name for restart functionality",
        }
