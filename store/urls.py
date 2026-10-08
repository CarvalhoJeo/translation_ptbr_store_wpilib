from django.contrib import admin
from django.urls import include, path

from transifex.views import cron_sync

urlpatterns = [
    path("admin/", admin.site.urls),
    path("contas/", include("allauth.urls")),
    path("cron/sync/", cron_sync, name="cron_sync"),
    path("", include("accounts.urls")),
]
