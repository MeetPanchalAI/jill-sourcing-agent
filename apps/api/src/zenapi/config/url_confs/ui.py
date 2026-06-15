"""URL config for the sourcing dashboard (bonus UX). Mounted at ``/ui/sourcing/``."""

from django.urls import path
from zenlib_agentos.zenlib.reusable_apps.sourcing import ui

urlpatterns = [
    path("", ui.roles_index, name="ui_sourcing_roles"),
    path("pipeline/", ui.pipeline, name="ui_sourcing_pipeline"),
    path("roles/new/", ui.create_role, name="ui_sourcing_role_create"),
    path("roles/<int:role_id>/", ui.role_detail, name="ui_sourcing_role_detail"),
    path("roles/<int:role_id>/source/", ui.start_sourcing,
         name="ui_sourcing_role_source"),
    path("outreach/<int:draft_id>/<str:action>/", ui.outreach_action,
         name="ui_sourcing_outreach_action"),
    path("linkedin/connect/", ui.linkedin_connect, name="ui_sourcing_linkedin_connect"),
]
