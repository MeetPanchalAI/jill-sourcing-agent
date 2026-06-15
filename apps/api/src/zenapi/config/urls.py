"""Root URL conf — assembles sub-routers from ``url_confs/``."""

from django.urls import include, path

urlpatterns = [
    path("", include("zenapi.config.url_confs.urls")),
    path("api/v1/email/", include("zenapi.config.url_confs.email")),
    path("api/v1/sourcing/", include("zenapi.config.url_confs.sourcing")),
    path("ui/sourcing/", include("zenapi.config.url_confs.ui")),
]
