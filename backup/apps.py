from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured


class BackupConfig(AppConfig):
    name = "backup"

    def ready(self):
        from django.conf import settings

        if settings.REQUIRE_AUTH and not settings.APP_PASSWORD:
            raise ImproperlyConfigured(
                "REQUIRE_AUTH is enabled but APP_PASSWORD is empty. "
                "Set the APP_PASSWORD environment variable or disable REQUIRE_AUTH."
            )
