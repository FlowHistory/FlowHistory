from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("health/", views.health_check, name="health_check"),
    path("api/backup/", views.api_create_backup, name="api_create_backup"),
]
