"""URL config for the sourcing app. Mounted at ``/api/v1/sourcing/``."""

from zenlib_agentos.zenlib.reusable_apps.sourcing.urls import (
    urlpatterns as _sourcing_urlpatterns,
)

urlpatterns = _sourcing_urlpatterns
