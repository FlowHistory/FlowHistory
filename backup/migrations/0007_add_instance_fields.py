"""Add multi-instance identity fields to NodeRedConfig and rename is_active."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("backup", "0006_remove_noderedconfig_name_and_more"),
    ]

    operations = [
        # Instance identity fields
        migrations.AddField(
            model_name="noderedconfig",
            name="name",
            field=models.CharField(default="Node-RED", max_length=100),
        ),
        migrations.AddField(
            model_name="noderedconfig",
            name="slug",
            field=models.SlugField(default="", max_length=100),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="noderedconfig",
            name="color",
            field=models.CharField(blank=True, default="", max_length=7),
        ),
        migrations.AddField(
            model_name="noderedconfig",
            name="is_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="noderedconfig",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
        # Source type fields
        migrations.AddField(
            model_name="noderedconfig",
            name="source_type",
            field=models.CharField(
                choices=[("local", "Local"), ("remote", "Remote")],
                default="local",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="noderedconfig",
            name="nodered_url",
            field=models.URLField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="noderedconfig",
            name="env_prefix",
            field=models.CharField(blank=True, default="", max_length=50),
        ),
        migrations.AddField(
            model_name="noderedconfig",
            name="poll_interval_seconds",
            field=models.PositiveIntegerField(default=60),
        ),
        # Rename is_active → schedule_enabled
        migrations.RenameField(
            model_name="noderedconfig",
            old_name="is_active",
            new_name="schedule_enabled",
        ),
    ]
