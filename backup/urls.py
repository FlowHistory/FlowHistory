from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("settings/", views.settings_view, name="settings"),
    path("backup/<int:backup_id>/", views.backup_detail, name="backup_detail"),
    path("backup/<int:backup_id>/download/", views.backup_download, name="backup_download"),
    path("health/", views.health_check, name="health_check"),
    path("api/backup/", views.api_create_backup, name="api_create_backup"),
    path("api/backup/<int:backup_id>/label/", views.api_set_label, name="api_set_label"),
    path("api/restore/<int:backup_id>/", views.api_restore_backup, name="api_restore_backup"),
    path("backup/<int:backup_id>/delete/", views.backup_delete, name="backup_delete"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
]
