"""LinkedIn account connection + one-click approve-and-send + spend estimate."""

from __future__ import annotations

import pytest
from django.db import connection
from zenlib_agentos.zenlib.reusable_apps.sourcing.models import (
    Candidate,
    LinkedInAccount,
    OutreachDraft,
    Role,
)

pytestmark = pytest.mark.django_db


def _set_rls(tenant):
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.current_tenant_id', %s, true)",
                    [str(tenant.id)])


# --- connect / status (API) --------------------------------------------------


def test_connect_then_status_hides_cookie(api_client, tenant_a, service_headers):
    h = service_headers(tenant_a)
    resp = api_client.post(
        "/api/v1/sourcing/linkedin/connect/",
        {"account_name": "Meet @ TL", "session_cookie": "li_at_secret"},
        format="json", **h,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "connected"
    assert body["invites_remaining"] == 20
    assert "session_cookie" not in body            # secret never serialized
    listing = api_client.get("/api/v1/sourcing/linkedin/", **h).json()
    assert listing["account"]["account_name"] == "Meet @ TL"


# --- one-click approve-and-send (UI) ----------------------------------------


def _linkedin_draft(tenant):
    role = Role.objects.create(title="Voice AI Eng", icp={})
    cand = Candidate.objects.create(
        linkedin_url="https://linkedin.com/in/alice", full_name="Alice"
    )
    return OutreachDraft.objects.create(
        candidate=cand, role=role,
        channel=OutreachDraft.Channel.LINKEDIN, body="Hi Alice",
    )


def test_approve_sends_invite_when_connected(client, tenant_a, in_tenant):
    with in_tenant(tenant_a):
        _set_rls(tenant_a)
        acct = LinkedInAccount.objects.create(
            account_name="Meet", status="connected", session_cookie="x"
        )
        draft = _linkedin_draft(tenant_a)
    client.post(f"/ui/sourcing/outreach/{draft.id}/approve/?tenant={tenant_a.id}")
    with in_tenant(tenant_a):
        _set_rls(tenant_a)
        draft.refresh_from_db()
        acct.refresh_from_db()
        assert draft.status == OutreachDraft.Status.SENT     # sent via account
        assert acct.invites_sent_today == 1


def test_approve_without_account_stays_approved(client, tenant_a, in_tenant):
    with in_tenant(tenant_a):
        _set_rls(tenant_a)
        draft = _linkedin_draft(tenant_a)
    client.post(f"/ui/sourcing/outreach/{draft.id}/approve/?tenant={tenant_a.id}")
    with in_tenant(tenant_a):
        _set_rls(tenant_a)
        draft.refresh_from_db()
        assert draft.status == OutreachDraft.Status.APPROVED  # queued, not sent


def test_daily_cap_blocks_send(client, tenant_a, in_tenant):
    with in_tenant(tenant_a):
        _set_rls(tenant_a)
        LinkedInAccount.objects.create(
            account_name="Meet", status="connected", session_cookie="x",
            daily_invite_limit=0,
        )
        draft = _linkedin_draft(tenant_a)
    client.post(f"/ui/sourcing/outreach/{draft.id}/approve/?tenant={tenant_a.id}")
    with in_tenant(tenant_a):
        _set_rls(tenant_a)
        draft.refresh_from_db()
        assert draft.status == OutreachDraft.Status.APPROVED  # cap reached


# --- spend estimate ---------------------------------------------------------


def test_role_costs_breakdown(api_client, tenant_a, service_headers):
    h = service_headers(tenant_a)
    role_id = api_client.post(
        "/api/v1/sourcing/roles/", {"title": "Voice AI Eng", "icp": {}},
        format="json", **h,
    ).json()["id"]
    run = api_client.post(f"/api/v1/sourcing/roles/{role_id}/source/", **h).json()
    api_client.post(
        f"/api/v1/sourcing/runs/{run['id']}/finalize/",
        {"status": "completed", "scanned_companies": 2, "found_candidates": 4,
         "fit_candidates": 1, "drafted": 6, "budget_used": 3},
        format="json", **h,
    )
    c = api_client.get(f"/api/v1/sourcing/roles/{role_id}/costs/", **h).json()
    # scrapes = scanned(2) + budget_used(3) + fit(1) = 6; llm = budget(3)+drafted(6)=9
    assert c["scrapes"] == 6
    assert c["llm_calls"] == 9
    assert c["total_usd"] == round((6 * 0.5 + 9 * 1.0) / 100, 2)  # $0.12
