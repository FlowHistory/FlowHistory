from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect

SAFE_METHODS = ("GET", "HEAD", "OPTIONS", "TRACE")
EXEMPT_PREFIXES = ("/login/", "/logout/", "/static/")
DEMO_MESSAGE = "Demo mode: changes are not saved."


class DemoModeMiddleware:
    """Block state-mutating requests when ``DEMO_MODE`` is enabled (ADR 0029).

    Lets safe HTTP methods through unchanged so every GET page renders
    normally. For unsafe methods on ``/api/`` paths we return the standard
    error envelope so the frontend's existing toast handler displays the
    demo message; for HTML form posts we attach a Django warning message
    and redirect back to the referring page.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.DEMO_MODE:
            return self.get_response(request)

        if request.method in SAFE_METHODS:
            return self.get_response(request)

        if any(request.path.startswith(p) for p in EXEMPT_PREFIXES):
            return self.get_response(request)

        if request.path.startswith("/api/"):
            return JsonResponse(
                {"status": "error", "message": DEMO_MESSAGE, "demo_mode": True}
            )

        messages.warning(request, DEMO_MESSAGE)
        return redirect(request.META.get("HTTP_REFERER") or "/")
