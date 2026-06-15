"""P6 gate (tests.md T5.1/T5.2): the OutreachDraft state machine forbids
draft -> sent without approval, at the model layer."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from zenlib_agentos.zenlib.reusable_apps.sourcing.models import (
    Candidate,
    OutreachDraft,
    Role,
)

pytestmark = pytest.mark.django_db


def _draft(tenant):
    role = Role.objects.create(title="Voice AI Eng", icp={})
    cand = Candidate.objects.create(
        linkedin_url="https://linkedin.com/in/a", full_name="Alice"
    )
    return OutreachDraft.objects.create(
        candidate=cand, role=role, channel=OutreachDraft.Channel.EMAIL, body="hi"
    )


def test_cannot_send_directly_from_draft(tenant_a, in_tenant):
    with in_tenant(tenant_a):
        d = _draft(tenant_a)
        assert d.status == OutreachDraft.Status.DRAFT
        with pytest.raises(ValidationError):
            d.mark_sent()  # approval required


def test_approve_then_send(tenant_a, in_tenant):
    with in_tenant(tenant_a):
        d = _draft(tenant_a)
        d.approve(by="recruiter@acme.com")
        assert d.status == OutreachDraft.Status.APPROVED
        assert d.approved_by == "recruiter@acme.com"
        d.mark_sent()
        assert d.status == OutreachDraft.Status.SENT
        assert d.sent_at is not None


def test_double_approve_rejected(tenant_a, in_tenant):
    with in_tenant(tenant_a):
        d = _draft(tenant_a)
        d.approve(by="x")
        with pytest.raises(ValidationError):
            d.approve(by="y")  # already approved


def test_reject_from_draft_and_approved(tenant_a, in_tenant):
    with in_tenant(tenant_a):
        d = _draft(tenant_a)
        d.reject(reason="not a fit")
        assert d.status == OutreachDraft.Status.REJECTED
        with pytest.raises(ValidationError):
            d.mark_sent()  # cannot send a rejected draft
