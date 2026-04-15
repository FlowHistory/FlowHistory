import time

from django.conf import settings
from django.shortcuts import redirect, render

EXEMPT_PATHS = ("/login/", "/health/", "/static/")

MAX_LOGIN_ATTEMPTS = 5
LOGIN_ATTEMPT_WINDOW = 300  # seconds (5 minutes)
LOCKOUT_DURATION = 900  # seconds (15 minutes)

# {ip: [(timestamp, ...), ...]}  — list of failed-attempt timestamps
_failed_attempts: dict[str, list[float]] = {}


def _get_client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _is_locked_out(ip):
    """Return True if *ip* has exceeded the failure threshold."""
    attempts = _failed_attempts.get(ip)
    if not attempts:
        return False
    now = time.monotonic()
    # Prune attempts outside the window
    cutoff = now - LOGIN_ATTEMPT_WINDOW
    attempts[:] = [t for t in attempts if t > cutoff]
    if not attempts:
        del _failed_attempts[ip]
        return False
    if len(attempts) >= MAX_LOGIN_ATTEMPTS:
        # Locked out until LOCKOUT_DURATION after the most recent failure
        return (now - attempts[-1]) < LOCKOUT_DURATION
    return False


def record_failed_attempt(ip):
    _failed_attempts.setdefault(ip, []).append(time.monotonic())


def clear_failed_attempts(ip):
    _failed_attempts.pop(ip, None)


class SimpleAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.REQUIRE_AUTH:
            return self.get_response(request)

        if any(request.path.startswith(p) for p in EXEMPT_PATHS):
            # Check lockout on login POST before allowing through
            if request.path == "/login/" and request.method == "POST":
                ip = _get_client_ip(request)
                if _is_locked_out(ip):
                    return render(
                        request,
                        "backup/login.html",
                        {"error": "Too many failed attempts. Please try again later."},
                    )
            return self.get_response(request)

        if request.session.get("authenticated"):
            return self.get_response(request)

        return redirect("login")
