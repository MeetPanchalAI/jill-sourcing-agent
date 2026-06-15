"""Cross-tenant isolation for the sourcing models — the P1 gate (tests.md T1).

Modeled on ``tests/test_multitenant.py``. Proves that rows created under one
tenant are invisible to another: the auto-filtering manager scopes querysets to
``context.current_tenant``, and Postgres RLS enforces it at the DB layer.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from zenlib.reusable_apps.multitenant import context
from zenlib_agentos.zenlib.reusable_apps.sourcing.models import (
    Candidate,
    OutreachDraft,
    Role,
    Score,
)

pytestmark = pytest.mark.django_db


def _set_rls(tenant):
    """Mimic MultitenantRLSMiddleware for the test connection."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT set_config('app.current_tenant_id', %s, true)",
            [str(tenant.id)],
        )


# --- T1.1 cross-tenant denial ------------------------------------------------


def test_role_invisible_across_tenants(tenant_a, tenant_b, in_tenant):
    with in_tenant(tenant_a):
        _set_rls(tenant_a)
        role = Role.objects.create(title="Voice AI Eng", icp={})
        cand = Candidate.objects.create(
            linkedin_url="https://linkedin.com/in/alice", full_name="Alice"
        )
        Score.objects.create(
            candidate=cand, role=role, score=88, verdict=Score.Verdict.FIT
        )
        OutreachDraft.objects.create(
            candidate=cand, role=role,
            channel=OutreachDraft.Channel.EMAIL, body="hi",
        )

    # Tenant B sees nothing.
    with in_tenant(tenant_b):
        _set_rls(tenant_b)
        assert Role.objects.count() == 0
        assert Candidate.objects.count() == 0
        assert Score.objects.count() == 0
        assert OutreachDraft.objects.count() == 0
        with pytest.raises(Role.DoesNotExist):
            Role.objects.get(id=role.id)

    # Tenant A still sees its own.
    with in_tenant(tenant_a):
        _set_rls(tenant_a)
        assert Role.objects.get(id=role.id).title == "Voice AI Eng"


# --- T1.2 tenant auto-populate ----------------------------------------------


def test_tenant_autopopulated_from_context(tenant_a, in_tenant):
    with in_tenant(tenant_a):
        role = Role.objects.create(title="x", icp={})
        assert role.tenant_id == tenant_a.id


def test_save_without_tenant_raises(db):
    # No tenant in context and none passed → ValidationError from the base model.
    context.current_tenant.set(None)
    with pytest.raises(ValidationError):
        Role.objects.create(title="orphan", icp={})


# --- T1.3 soft-delete --------------------------------------------------------


def test_soft_delete_hides_row(tenant_a, in_tenant):
    with in_tenant(tenant_a):
        role = Role.objects.create(title="temp", icp={})
        role.delete()
        assert Role.objects.filter(id=role.id).count() == 0
        assert Role.objects.all_with_deleted().filter(id=role.id).count() == 1
