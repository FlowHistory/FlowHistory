import tomllib
from pathlib import Path

from django.conf import settings

_APP_VERSION = tomllib.loads(
    (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
)["project"]["version"]


def auth_context(request):
    return {"require_auth": settings.REQUIRE_AUTH, "app_version": _APP_VERSION}
