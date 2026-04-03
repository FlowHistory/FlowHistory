from django.conf import settings


def auth_context(request):
    return {"require_auth": settings.REQUIRE_AUTH}
