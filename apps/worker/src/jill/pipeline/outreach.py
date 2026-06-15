"""Outreach stage: draft personalized invites per channel as ``draft`` records.

Drafting never sends — it only stages drafts for a human to approve (C17). One
draft per (candidate, role, channel); idempotent on re-run.
"""

from __future__ import annotations

from ..agent.drafting import DraftContext, Drafter

DEFAULT_CHANNELS = ("linkedin", "email")


def draft_outreach(
    client,
    drafter: Drafter,
    *,
    candidate_id: int,
    role_id: int,
    ctx: DraftContext,
    channels=DEFAULT_CHANNELS,
) -> list:
    """Create a draft per channel for a fit lead. Returns the upserted records."""
    drafts = []
    for channel in channels:
        result = drafter.draft(ctx, channel)
        up = client.create_outreach(
            candidate=candidate_id,
            role=role_id,
            channel=channel,
            subject=result.subject,
            body=result.body,
        )
        drafts.append(up)
    return drafts
