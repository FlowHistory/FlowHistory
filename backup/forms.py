from django import forms

from .models import NodeRedConfig

TW_INPUT = "block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 dark:bg-gray-800 dark:border-gray-600 dark:text-gray-100"
TW_SELECT = TW_INPUT
TW_CHECKBOX = "h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500 dark:border-gray-600 dark:bg-gray-800"


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
            "name": forms.TextInput(attrs={"class": TW_INPUT}),
            "flows_path": forms.TextInput(attrs={"class": TW_INPUT}),
            "is_active": forms.CheckboxInput(attrs={"class": TW_CHECKBOX}),
            "backup_frequency": forms.Select(attrs={"class": TW_SELECT}),
            "backup_time": forms.TimeInput(attrs={"class": TW_INPUT, "type": "time"}),
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
                attrs={"class": TW_SELECT},
            ),
            "watch_enabled": forms.CheckboxInput(attrs={"class": TW_CHECKBOX}),
            "watch_debounce_seconds": forms.NumberInput(attrs={"class": TW_INPUT, "min": "5"}),
            "backup_credentials": forms.CheckboxInput(attrs={"class": TW_CHECKBOX}),
            "backup_settings": forms.CheckboxInput(attrs={"class": TW_CHECKBOX}),
            "max_backups": forms.NumberInput(attrs={"class": TW_INPUT, "min": "1"}),
            "max_age_days": forms.NumberInput(attrs={"class": TW_INPUT, "min": "1"}),
            "restart_on_restore": forms.CheckboxInput(attrs={"class": TW_CHECKBOX}),
            "nodered_container_name": forms.TextInput(attrs={"class": TW_INPUT}),
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
