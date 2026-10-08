from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("contas/", include("allauth.urls")),
    path("", include("accounts.urls")),
]
