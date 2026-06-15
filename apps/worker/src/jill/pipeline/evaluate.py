"""Per-lead evaluation: enrich → score → (if fit) expand + draft.

Shared by the Temporal ``evaluate_candidate`` activity and the in-process
``run_sourcing`` runner so the judgment logic lives in exactly one place.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..agent.drafting import build_context
from ..agent.scoring import Scorer
from ..brightdata.base import BrightdataClient
from ..brightdata.errors import BrightdataNotFound
from .expand import Budget, expand_lead
from .outreach import draft_outreach
from .score import score_candidate


@dataclass
class EvalOutcome:
    is_fit: bool
    drafted: int
    skipped: bool
    prev_employer_companies: list[str] = field(default_factory=list)
    network_leads: list[dict] = field(default_factory=list)


def evaluate_lead(
    client,
    brightdata: BrightdataClient,
    scorer: Scorer,
    drafter,
    *,
    role_id: int,
    run_id: int | None,
    candidate_id: int,
    linkedin_url: str,
    role_title: str,
    icp: dict,
    depth: int,
    max_depth: int,
    budget: Budget,
    fetched_at: str,
) -> EvalOutcome:
    try:
        profile = brightdata.profile(linkedin_url)
    except BrightdataNotFound:
        return EvalOutcome(is_fit=False, drafted=0, skipped=True)

    client.upsert_enrichment(
        candidate=candidate_id,
        raw=asdict(profile),
        experiences=[asdict(e) for e in profile.experiences],
        skills=profile.skills,
        fetched_at=fetched_at,
    )
    outcome = score_candidate(
        client, scorer, candidate_id=candidate_id, role_id=role_id, icp=icp,
        profile=profile,
    )
    if not outcome.is_fit:
        return EvalOutcome(is_fit=False, drafted=0, skipped=False)

    exp = expand_lead(
        client, brightdata, role_id=role_id, run_id=run_id,
        lead_candidate_id=candidate_id, lead_profile=profile, depth=depth,
        max_depth=max_depth, budget=budget,
    )
    ctx = build_context(
        profile, role_title=role_title, reasons=outcome.result.reasons,
        source_kind="recent_joiner",
    )
    drafts = draft_outreach(
        client, drafter, candidate_id=candidate_id, role_id=role_id, ctx=ctx,
    )
    return EvalOutcome(
        is_fit=True, drafted=len(drafts), skipped=False,
        prev_employer_companies=exp.prev_employer_companies,
        network_leads=exp.network_leads,
    )
