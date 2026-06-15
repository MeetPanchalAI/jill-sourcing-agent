"""Base URLs: portal redirect, admin, health, knox auth."""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from django.views.generic import RedirectView


def health(_request):
    return JsonResponse({"ok": True})


urlpatterns = [
    # Land visitors on the sourcing portal.
    path("", RedirectView.as_view(url="/ui/sourcing/", permanent=False)),
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("api/auth/", include("knox.urls")),
]
