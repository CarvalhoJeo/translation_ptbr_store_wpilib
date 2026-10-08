from django.urls import path

from . import views

urlpatterns = [
    path("loja/", views.catalog, name="shop_catalog"),
    path("loja/<int:product_id>/resgatar/", views.request_view, name="shop_request"),
    path("loja/resgates/<int:pk>/cancelar/", views.cancel_view, name="shop_cancel"),
]
