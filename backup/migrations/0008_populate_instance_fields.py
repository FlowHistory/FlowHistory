"""Populate instance fields on existing NodeRedConfig rows."""

from django.db import migrations
from django.utils import timezone
from django.utils.text import slugify


RESERVED_SLUGS = {"add", "api"}


def populate_instance_fields(apps, schema_editor):
    NodeRedConfig = apps.get_model("backup", "NodeRedConfig")
    used_slugs = set()

    for config in NodeRedConfig.objects.all():
        # Generate unique slug
        base = slugify(config.name) or "node-red"
        if base in RESERVED_SLUGS:
            base = f"{base}-instance"
        slug, n = base, 1
        while slug in used_slugs:
            n += 1
            slug = f"{base}-{n}"
        used_slugs.add(slug)

        config.slug = slug
        config.created_at = timezone.now()
        config.env_prefix = "LOCAL"
        config.save(update_fields=["slug", "created_at", "env_prefix"])


class Migration(migrations.Migration):

    dependencies = [
        ("backup", "0007_add_instance_fields"),
    ]

    operations = [
        migrations.RunPython(populate_instance_fields, migrations.RunPython.noop),
    ]
