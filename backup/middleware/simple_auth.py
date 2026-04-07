from django.conf import settings
from django.shortcuts import redirect

EXEMPT_PATHS = ("/login/", "/health/", "/static/")


class SimpleAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.REQUIRE_AUTH:
            return self.get_response(request)

        if any(request.path.startswith(p) for p in EXEMPT_PATHS):
            return self.get_response(request)

        if request.session.get("authenticated"):
            return self.get_response(request)

        return redirect("login")
