from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("backup.urls")),
]

handler404 = "backup.views.custom_404"
handler500 = "backup.views.custom_500"
