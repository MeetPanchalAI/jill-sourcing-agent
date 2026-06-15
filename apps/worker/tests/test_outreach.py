"""P6 gate (tests.md §5): outreach drafting + delivery — grounded drafts, per-
channel persistence, and no send without approval. All mock; no network."""

from __future__ import annotations

import logging

import pytest
from jill.agent.drafting import TemplateDrafter, build_context
from jill.brightdata.mock import MockBrightdataClient
from jill.outreach.deliver import MockDeliverer, NotApproved, deliver_if_approved
from jill.pipeline.outreach import draft_outreach
from jill.webpy.fake import FakeWebPy

BD = MockBrightdataClient()


def _ctx():
    profile = BD.profile("https://linkedin.com/in/alice-nguyen")
    return profile, build_context(
        profile, role_title="Voice AI Engineer",
        reasons=["Matches must-have skills: Python, Realtime Audio"],
        source_kind="recent_joiner",
    )


# --- drafting (grounded) -----------------------------------------------------


def test_email_draft_has_subject_and_grounded_body():
    profile, ctx = _ctx()
    res = TemplateDrafter().draft(ctx, "email")
    assert res.subject
    assert "Alice" in res.body
    assert profile.current_company in res.body  # "Vapi" — a real fact
    assert "Voice AI Engineer" in res.body


def test_linkedin_draft_has_no_subject():
    _, ctx = _ctx()
    res = TemplateDrafter().draft(ctx, "linkedin")
    assert res.subject == ""
    assert "Alice" in res.body


def test_draft_does_not_invent_facts():
    profile, ctx = _ctx()
    body = TemplateDrafter().draft(ctx, "email").body
    # No employer that isn't in the candidate's real history.
    real = {profile.current_company, *(e.company for e in profile.experiences)}
    for invented in ("Google", "OpenAI", "Microsoft"):
        assert invented not in real  # sanity
        assert invented not in body


# --- T5.x persistence + idempotency -----------------------------------------


def test_draft_outreach_persists_one_per_channel_idempotently():
    client = FakeWebPy()
    _, ctx = _ctx()
    first = draft_outreach(client, TemplateDrafter(), candidate_id=7, role_id=1,
                           ctx=ctx)
    assert len(first) == 2
    assert all(d.data["status"] == "draft" for d in first)
    # Re-running drafts nothing new.
    draft_outreach(client, TemplateDrafter(), candidate_id=7, role_id=1, ctx=ctx)
    assert len(client.outreach) == 2


# --- T5.1 / T5.4 no send without approval -----------------------------------


def test_delivery_refuses_unapproved_draft():
    draft = {"status": "draft", "channel": "email", "body": "hi", "subject": "x"}
    with pytest.raises(NotApproved):
        deliver_if_approved(MockDeliverer(), draft, to="alice@example.com")


def test_delivery_sends_approved_draft_without_network(caplog):
    deliverer = MockDeliverer()
    draft = {"status": "approved", "channel": "linkedin",
             "body": "Hi Alice — open to a chat?", "subject": ""}
    with caplog.at_level(logging.INFO, logger="jill.outreach"):
        result = deliver_if_approved(deliverer, draft,
                                     to="https://linkedin.com/in/alice-nguyen")
    assert result["status"] == "sent"
    assert deliverer.sent and deliverer.sent[0]["channel"] == "linkedin"
    # PII-safe: the body text never appears in the logs.
    assert "open to a chat" not in "\n".join(r.getMessage() for r in caplog.records)
