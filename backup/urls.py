from django.conf import settings
from django.urls import include, path

from . import views

urlpatterns = [
    # Aggregate dashboard
    path("", views.dashboard, name="dashboard"),
    # Instance management
    path("instance/add/", views.instance_add, name="instance_add"),
    # Instance-scoped pages
    path("instance/<slug:slug>/", views.instance_dashboard, name="instance_dashboard"),
    path(
        "instance/<slug:slug>/settings/",
        views.instance_settings,
        name="instance_settings",
    ),
    path(
        "instance/<slug:slug>/backup/<int:backup_id>/",
        views.backup_detail,
        name="backup_detail",
    ),
    path(
        "instance/<slug:slug>/backup/<int:backup_id>/download/",
        views.backup_download,
        name="backup_download",
    ),
    path(
        "instance/<slug:slug>/backup/<int:backup_id>/delete/",
        views.backup_delete,
        name="backup_delete",
    ),
    path(
        "instance/<slug:slug>/diff/<int:backup_id>/",
        views.diff_view,
        name="diff_vs_previous",
    ),
    path(
        "instance/<slug:slug>/diff/<int:backup_id>/<int:compare_id>/",
        views.diff_view,
        name="diff_compare",
    ),
    path("instance/<slug:slug>/delete/", views.instance_delete, name="instance_delete"),
    # Instance-scoped API
    path(
        "api/instance/<slug:slug>/backup/",
        views.api_create_backup,
        name="api_create_backup",
    ),
    path(
        "api/instance/<slug:slug>/import/",
        views.api_import_backup,
        name="api_import_backup",
    ),
    path(
        "api/instance/<slug:slug>/backup/<int:backup_id>/label/",
        views.api_set_label,
        name="api_set_label",
    ),
    path(
        "api/instance/<slug:slug>/backup/<int:backup_id>/notes/",
        views.api_set_notes,
        name="api_set_notes",
    ),
    path(
        "api/instance/<slug:slug>/backup/<int:backup_id>/pin/",
        views.api_toggle_pin,
        name="api_toggle_pin",
    ),
    path(
        "api/instance/<slug:slug>/bulk/", views.api_bulk_action, name="api_bulk_action"
    ),
    path(
        "api/instance/<slug:slug>/restore/<int:backup_id>/",
        views.api_restore_backup,
        name="api_restore_backup",
    ),
    path(
        "api/instance/<slug:slug>/clear-error/",
        views.api_clear_error,
        name="api_clear_error",
    ),
    path(
        "api/instance/<slug:slug>/test-connection/",
        views.api_test_connection,
        name="api_test_connection",
    ),
    path(
        "api/instance/<slug:slug>/notifications/test/",
        views.api_test_notification,
        name="api_test_notification",
    ),
    # Non-instance routes
    path("health/", views.health_check, name="health_check"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
]

if settings.METRICS_ENABLED:
    urlpatterns += [path("", include("django_prometheus.urls"))]
