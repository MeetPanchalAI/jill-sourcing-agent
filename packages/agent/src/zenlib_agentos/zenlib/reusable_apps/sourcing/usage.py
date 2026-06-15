"""Spend estimation — visualize credit/money burned per role.

We don't depend on a billing API (Brightdata/Instantly usage endpoints may not be
available); instead we derive cost from the work the run actually did, which is
exactly what Temporal/the run counters already track:

  * Brightdata scrapes  = company scans + profile enrichments + network lookups
  * Claude calls        = one score per candidate + one draft per channel
  * Outreach            = invites/emails actually sent

Multiplied by a configurable price table (cents). Estimates, clearly labelled.
"""

from __future__ import annotations

from django.conf import settings
from django.db.models import Sum

from .models import OutreachDraft, SourcingRun

DEFAULT_PRICES = {
    "scrape_cents": 0.5,   # Brightdata per record
    "llm_cents": 1.0,      # Claude per score/draft call
    "invite_cents": 0.0,   # LinkedIn invite via the connected account (free)
    "email_cents": 0.1,    # Instantly per email
}


def prices() -> dict:
    return {**DEFAULT_PRICES, **getattr(settings, "SOURCING_PRICES", {})}


def role_cost(role) -> dict:
    """Estimated spend for a role, aggregated across its runs."""
    p = prices()
    # Aggregate the counters in Postgres rather than looping over run rows.
    agg = SourcingRun.objects.filter(role=role).aggregate(
        scanned=Sum("scanned_companies"), budget=Sum("budget_used"),
        fit=Sum("fit_candidates"), drafted=Sum("drafted"),
    )
    scanned, budget = agg["scanned"] or 0, agg["budget"] or 0
    fit, drafted = agg["fit"] or 0, agg["drafted"] or 0
    scrapes = scanned + budget + fit       # company scan + profile enrich + network
    llm_calls = budget + drafted           # one score per candidate + one per draft

    sent = OutreachDraft.objects.filter(role=role, status=OutreachDraft.Status.SENT)
    invites = sent.filter(channel=OutreachDraft.Channel.LINKEDIN).count()
    emails = sent.filter(channel=OutreachDraft.Channel.EMAIL).count()

    bd = scrapes * p["scrape_cents"]
    llm = llm_calls * p["llm_cents"]
    out = invites * p["invite_cents"] + emails * p["email_cents"]
    total = bd + llm + out
    return {
        "scrapes": scrapes, "llm_calls": llm_calls,
        "invites_sent": invites, "emails_sent": emails,
        "brightdata_cents": round(bd, 2), "llm_cents": round(llm, 2),
        "outreach_cents": round(out, 2), "total_cents": round(total, 2),
        "total_usd": round(total / 100, 2),
    }
