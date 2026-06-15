"""Portal: create a role and start sourcing from the dashboard, self-contained —
the in-process runner mirrors the durable workflow and writes via the ORM."""

from __future__ import annotations

import pytest
from django.db import connection
from zenlib.reusable_apps.multitenant import context
from zenlib_agentos.zenlib.reusable_apps.sourcing.agent_runner import (
    run_sourcing_inprocess,
)
from zenlib_agentos.zenlib.reusable_apps.sourcing.models import (
    OutreachDraft,
    Role,
    Score,
    SourcingRun,
)

pytestmark = pytest.mark.django_db


def _set_rls(tenant):
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.current_tenant_id', %s, true)",
                    [str(tenant.id)])


def _role(seed="Vapi"):
    return Role.objects.create(
        title="Voice AI Engineer", status=Role.Status.SOURCING,
        icp={
            "target_companies": [{"name": seed}],
            "must_have_skills": ["Python"],
            "rubric": [{"name": "Python", "type": "skill", "skill": "Python",
                        "weight": 2}],
        },
    )


def test_inprocess_runner_produces_scored_leads_and_drafts(tenant_a, in_tenant):
    with in_tenant(tenant_a):
        _set_rls(tenant_a)
        run = run_sourcing_inprocess(_role())
        assert run.status == SourcingRun.Status.COMPLETED
        assert run.scanned_companies > 0
        assert Score.objects.filter(role=run.role).exists()
        assert OutreachDraft.objects.filter(role=run.role).exists()


def test_inprocess_runner_is_idempotent(tenant_a, in_tenant):
    with in_tenant(tenant_a):
        _set_rls(tenant_a)
        role = _role()
        run_sourcing_inprocess(role)
        first = Score.objects.filter(role=role).count()
        run_sourcing_inprocess(role)  # re-run must converge, not duplicate
        assert Score.objects.filter(role=role).count() == first


def test_create_role_from_portal(client, tenant_a):
    resp = client.post(
        f"/ui/sourcing/roles/new/?tenant={tenant_a.id}",
        {"title": "Backend Eng", "company": "Retell AI", "skills": "Python, Go"},
    )
    assert resp.status_code == 302
    token = context.current_tenant.set(tenant_a)
    try:
        _set_rls(tenant_a)
        role = Role.objects.get(title="Backend Eng")
        assert role.icp["must_have_skills"] == ["Python", "Go"]
        assert any(c["type"] == "skill" for c in role.icp["rubric"])
    finally:
        context.current_tenant.reset(token)


def test_start_sourcing_from_portal(client, tenant_a, in_tenant):
    with in_tenant(tenant_a):
        _set_rls(tenant_a)
        role = _role()
    resp = client.post(f"/ui/sourcing/roles/{role.id}/source/?tenant={tenant_a.id}")
    assert resp.status_code == 302
    page = client.get(f"/ui/sourcing/roles/{role.id}/?tenant={tenant_a.id}")
    assert page.status_code == 200
    with in_tenant(tenant_a):
        _set_rls(tenant_a)
        assert Score.objects.filter(role=role).exists()
