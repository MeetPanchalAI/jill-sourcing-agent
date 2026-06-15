"""P9: the dashboard renders tenant-scoped leads with provenance and approves
outreach — server-rendered, RLS-respecting."""

from __future__ import annotations

import pytest
from django.db import connection
from zenlib_agentos.zenlib.reusable_apps.sourcing.models import (
    Candidate,
    LeadEdge,
    OutreachDraft,
    Role,
    Score,
    TargetCompany,
)

pytestmark = pytest.mark.django_db


def _set_rls(tenant):
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.current_tenant_id', %s, true)",
                    [str(tenant.id)])


def _seed(tenant):
    role = Role.objects.create(title="Voice AI Eng", icp={})
    company = TargetCompany.objects.create(role=role, name="Vapi")
    cand = Candidate.objects.create(
        linkedin_url="https://linkedin.com/in/alice", full_name="Alice Nguyen",
        current_company="Vapi", current_title="Founding Engineer",
    )
    Score.objects.create(candidate=cand, role=role, score=92,
                         verdict=Score.Verdict.FIT, reasons=["Matches Python, WebRTC"])
    LeadEdge.objects.create(role=role, to_candidate=cand,
                            kind=LeadEdge.Kind.RECENT_JOINER, from_company=company)
    draft = OutreachDraft.objects.create(
        candidate=cand, role=role, channel=OutreachDraft.Channel.EMAIL, body="Hi Alice"
    )
    return role, cand, draft


def test_role_detail_renders_leads_and_provenance(client, tenant_a, in_tenant):
    with in_tenant(tenant_a):
        _set_rls(tenant_a)
        role, _, _ = _seed(tenant_a)
    resp = client.get(f"/ui/sourcing/roles/{role.id}/?tenant={tenant_a.id}")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Alice Nguyen" in body
    assert "92" in body
    assert "recent_joiner" in body  # provenance chip
    assert "Approve" in body        # draft action present


def test_ui_is_tenant_isolated(client, tenant_a, tenant_b, in_tenant):
    with in_tenant(tenant_a):
        _set_rls(tenant_a)
        role, _, _ = _seed(tenant_a)
    # Viewing tenant B must not reveal tenant A's role.
    resp = client.get(f"/ui/sourcing/roles/{role.id}/?tenant={tenant_b.id}")
    assert resp.status_code in (302, 404)
    if resp.status_code == 200:
        assert "Alice Nguyen" not in resp.content.decode()


def test_approve_from_ui_transitions_draft(client, tenant_a, in_tenant):
    with in_tenant(tenant_a):
        _set_rls(tenant_a)
        _, _, draft = _seed(tenant_a)
    resp = client.post(
        f"/ui/sourcing/outreach/{draft.id}/approve/?tenant={tenant_a.id}"
    )
    assert resp.status_code == 302
    with in_tenant(tenant_a):
        _set_rls(tenant_a)
        draft.refresh_from_db()
        assert draft.status == OutreachDraft.Status.APPROVED
        assert draft.approved_by == "ui"
