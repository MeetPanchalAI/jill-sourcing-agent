"""Per-lead evaluation: enrich → score → (if fit) expand + draft.

Shared by the Temporal ``evaluate_candidate`` activity and the in-process
``run_sourcing`` runner so the judgment logic lives in exactly one place.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

from ..agent.drafting import build_context
from ..agent.scoring import Scorer
from ..agent.triage import surface_triage
from ..brightdata.base import BrightdataClient
from ..brightdata.errors import BrightdataNotFound
from .expand import Budget, expand_lead
from .outreach import draft_outreach
from .score import score_candidate

logger = logging.getLogger("jill.pipeline")


@dataclass
class EvalOutcome:
    is_fit: bool
    drafted: int
    skipped: bool
    surfaced: bool = False  # passed triage → on the shortlist + expand the frontier
    score: int | None = None
    verdict: str = ""
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
    expand_min_score: int = 0,
    fetched_at: str,
) -> EvalOutcome:
    try:
        profile = brightdata.profile(linkedin_url)
    except BrightdataNotFound:
        logger.info("  +- LEAD #%-4s %s", candidate_id, linkedin_url)
        logger.info("  |    enrich  no public profile data - skipped")
        return EvalOutcome(is_fit=False, drafted=0, skipped=True)

    logger.info("  +- LEAD #%-4s %s  (depth %d)",
                candidate_id, profile.full_name or linkedin_url, depth)
    logger.info("  |    enrich  %d roles, %d skills, %s%s",
                len(profile.experiences), len(profile.skills),
                profile.current_title or profile.headline or "-",
                f" @ {profile.current_company}" if profile.current_company else "")

    # Backfill the candidate's display fields from the deep profile so leads show
    # a real name/title even when seeded by bare URL (idempotent upsert on URL).
    client.upsert_candidate(
        linkedin_url=linkedin_url,
        full_name=profile.full_name,
        headline=profile.headline,
        current_company=profile.current_company,
        current_title=profile.current_title,
        location=profile.location,
        started_current_role_at=profile.started_at,
    )
    client.upsert_enrichment(
        candidate=candidate_id,
        raw=asdict(profile),
        experiences=[asdict(e) for e in profile.experiences],
        skills=profile.skills,
        fetched_at=fetched_at,
    )
    # Two independent judgements:
    #   triage  = RECALL  — keep on the shortlist + expand (permissive, reliable signals)
    #   fit     = PRECISION — rank the shortlist (the strict rubric)
    triage = surface_triage(profile, icp)
    outcome = score_candidate(
        client, scorer, candidate_id=candidate_id, role_id=role_id, icp=icp,
        profile=profile,
    )
    score, verdict = outcome.result.score, outcome.result.verdict
    model = getattr(scorer, "model_id", "")

    # Fit ranks; it does NOT delete from the list — a DROP still stays, ranked lower.
    if outcome.is_fit:
        logger.info("  |    score   %3d/100  ->  FIT    [%s]", score, model)
        if outcome.result.reasons:
            logger.info("  |            + %s", "  ".join(outcome.result.reasons[:3]))
    else:
        logger.info("  |    score   %3d/100  ->  DROP   [%s]", score, model)
        logger.info("  |            x %s",
                    outcome.result.drop_reason or outcome.result.summary or "below threshold")
    logger.info("  |    triage  %s — %s",
                "KEEP" if triage.keep else "REJECT", triage.reason)

    # Grow the tree from triage-kept candidates, but not from low-signal noise:
    # seed-company employees (depth 0) are always explored; anyone discovered
    # deeper must clear the expansion floor. Keeps weak leads on the list (recall)
    # without letting them seed more noise.
    can_expand = triage.keep and (depth == 0 or (score or 0) >= expand_min_score)
    exp = None
    if can_expand:
        exp = expand_lead(
            client, brightdata, role_id=role_id, run_id=run_id,
            lead_candidate_id=candidate_id, lead_profile=profile, depth=depth,
            max_depth=max_depth, budget=budget,
        )
        if exp.prev_employer_companies or exp.network_leads:
            logger.info("  |    expand  +%d prev-employer compan%s, +%d network peer%s  ->  frontier",
                        len(exp.prev_employer_companies),
                        "y" if len(exp.prev_employer_companies) == 1 else "ies",
                        len(exp.network_leads),
                        "" if len(exp.network_leads) == 1 else "s")

    # Draft outreach only for high-confidence fits — don't auto-message the whole list.
    drafts: list = []
    if outcome.is_fit:
        ctx = build_context(
            profile, role_title=role_title, reasons=outcome.result.reasons,
            source_kind="recent_joiner",
        )
        drafts = draft_outreach(
            client, drafter, candidate_id=candidate_id, role_id=role_id, ctx=ctx,
        )
        logger.info("  |    draft   %d invite%s staged for approval",
                    len(drafts), "" if len(drafts) == 1 else "s")

    return EvalOutcome(
        is_fit=outcome.is_fit, drafted=len(drafts), skipped=False, surfaced=triage.keep,
        score=score, verdict=verdict,
        prev_employer_companies=exp.prev_employer_companies if exp else [],
        network_leads=exp.network_leads if exp else [],
    )
