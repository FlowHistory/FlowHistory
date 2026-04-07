from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("backup", "0003_backuprecord_notes"),
    ]

    operations = [
        migrations.AddField(
            model_name="backuprecord",
            name="is_pinned",
            field=models.BooleanField(default=False),
        ),
    ]
