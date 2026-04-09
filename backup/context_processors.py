import os
import tomllib
from pathlib import Path

from django.conf import settings

_APP_VERSION = tomllib.loads(
    (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
)["project"]["version"]

_GIT_COMMIT_SHORT = os.environ.get("GIT_COMMIT_SHORT", "dev")
_BUILD_DATE = os.environ.get("BUILD_DATE", "")
_BUILD_REPO = os.environ.get("BUILD_REPO", "")


def auth_context(request):
    return {
        "require_auth": settings.REQUIRE_AUTH,
        "app_version": _APP_VERSION,
        "git_commit_short": _GIT_COMMIT_SHORT,
        "build_date": _BUILD_DATE,
        "build_repo": _BUILD_REPO,
    }
