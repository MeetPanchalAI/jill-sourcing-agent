"""``manage.py create_tenant "Acme"`` — create a tenant and print its id.

A tenant is one recruiting company; its data is isolated from every other tenant
by row-level security. Idempotent on slug, so re-running is safe.
"""

import secrets

from django.core.management.base import BaseCommand
from zenlib.reusable_apps.multitenant.models import Tenant


class Command(BaseCommand):
    help = "Create a tenant (a recruiting company) and print its id."

    def add_arguments(self, parser):
        parser.add_argument("name", help="Display name, e.g. \"Acme Recruiting\".")
        parser.add_argument(
            "--slug", default=None,
            help="URL-safe key (defaults to a slugified name).",
        )

    def handle(self, *args, **opts):
        name = opts["name"]
        slug = opts["slug"] or name.lower().replace(" ", "-")
        tenant, created = Tenant.objects.get_or_create(
            slug=slug,
            defaults={"name": name, "service_token": secrets.token_hex(16)},
        )
        verb = "created" if created else "already exists"
        self.stdout.write(self.style.SUCCESS(
            f"tenant id {tenant.id} ({verb}): {tenant.name}"
        ))
