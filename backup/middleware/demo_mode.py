import re

from django.conf import settings
from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

SAFE_METHODS = ("GET", "HEAD", "OPTIONS", "TRACE")
EXEMPT_PREFIXES = ("/login/", "/logout/")
DEMO_MESSAGE = "Demo mode: changes are not saved."

# Hidden entirely in demo mode: /admin/ has its own auth and an enumerable
# login form. With REQUIRE_AUTH forced off and the URL public, a stale
# superuser in a reused volume would otherwise be a brute-force foothold.
BLOCKED_PREFIXES = ("/admin/",)

# GET endpoints that stream raw archive contents — blocked in demo mode so
# anonymous visitors can't download backups that may include flows_cred.json
# or settings.js from a real Node-RED instance.
BLOCKED_GET_PATTERNS = (re.compile(r"^/instance/[^/]+/backup/\d+/download/$"),)


class DemoModeMiddleware:
    """Block state-mutating requests when ``DEMO_MODE`` is enabled (ADR 0029).

    Lets safe HTTP methods through unchanged so every GET page renders
    normally, except for download endpoints that would expose raw archive
    bytes. For unsafe methods on ``/api/`` paths we return the standard
    error envelope so the frontend's existing toast handler displays the
    demo message; for HTML form posts we attach a Django warning message
    and redirect back to the referring page.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.DEMO_MODE:
            return self.get_response(request)

        if any(request.path.startswith(p) for p in BLOCKED_PREFIXES):
            raise Http404

        if request.method in SAFE_METHODS:
            if any(p.match(request.path) for p in BLOCKED_GET_PATTERNS):
                return self._reject_html(request)
            return self.get_response(request)

        if any(request.path.startswith(p) for p in EXEMPT_PREFIXES):
            return self.get_response(request)

        if request.path.startswith("/api/"):
            return JsonResponse(
                {"status": "error", "message": DEMO_MESSAGE, "demo_mode": True}
            )

        return self._reject_html(request)

    def _reject_html(self, request):
        messages.warning(request, DEMO_MESSAGE)
        # Referer is attacker-influenceable; validate same-host before
        # redirecting or the demo becomes an open-redirect to a phishing page.
        referer = request.META.get("HTTP_REFERER") or ""
        target = (
            referer
            if url_has_allowed_host_and_scheme(
                referer, allowed_hosts={request.get_host()}
            )
            else "/"
        )
        return redirect(target)
