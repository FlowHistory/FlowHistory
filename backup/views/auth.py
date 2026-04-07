from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@require_GET
def health_check(request):
    return JsonResponse({"status": "ok"})


def login_view(request):
    if request.method == "POST":
        password = request.POST.get("password", "")
        if password == settings.APP_PASSWORD:
            request.session["authenticated"] = True
            return redirect("dashboard")
        return render(request, "backup/login.html", {"error": "Invalid password"})
    return render(request, "backup/login.html")


@require_POST
def logout_view(request):
    request.session.flush()
    return redirect("login")


def custom_404(request, exception):
    messages.error(request, "Page not found.")
    return redirect("dashboard")


def custom_500(request):
    messages.error(request, "An unexpected error occurred.")
    return redirect("dashboard")
