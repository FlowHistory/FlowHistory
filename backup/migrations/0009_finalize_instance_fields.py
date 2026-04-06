"""Finalize instance field constraints: slug unique, created_at non-null."""

from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):

    dependencies = [
        ("backup", "0008_populate_instance_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="noderedconfig",
            name="slug",
            field=models.SlugField(max_length=100, unique=True),
        ),
        migrations.AlterField(
            model_name="noderedconfig",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, default=timezone.now),
            preserve_default=False,
        ),
    ]
