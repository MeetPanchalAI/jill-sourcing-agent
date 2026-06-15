"""URL config for the sourcing dashboard (bonus UX). Mounted at ``/ui/sourcing/``."""

from django.urls import path
from zenlib_agentos.zenlib.reusable_apps.sourcing import ui

urlpatterns = [
    path("", ui.roles_index, name="ui_sourcing_roles"),
    path("roles/<int:role_id>/", ui.role_detail, name="ui_sourcing_role_detail"),
    path("outreach/<int:draft_id>/<str:action>/", ui.outreach_action,
         name="ui_sourcing_outreach_action"),
    path("linkedin/connect/", ui.linkedin_connect, name="ui_sourcing_linkedin_connect"),
]
