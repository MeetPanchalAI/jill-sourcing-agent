"""Score stage: run the scorer against an enriched profile, persist the verdict.

Returns whether the candidate is a fit so the orchestrator knows whether to
expand the lead graph (P5) and draft outreach (P6). Drops are persisted too —
with their reason — so the funnel is auditable.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..agent.schemas import ScoreResult
from ..agent.scoring import Scorer
from ..brightdata.types import Profile


@dataclass
class ScoreOutcome:
    candidate_id: int
    result: ScoreResult

    @property
    def is_fit(self) -> bool:
        return self.result.verdict == "fit"


def score_candidate(
    client,
    scorer: Scorer,
    *,
    candidate_id: int,
    role_id: int,
    icp: dict,
    profile: Profile,
) -> ScoreOutcome:
    result = scorer.score(icp, profile)
    client.upsert_score(
        candidate=candidate_id,
        role=role_id,
        score=result.score,
        verdict=result.verdict,
        summary=result.summary,
        criteria=[c.model_dump() for c in result.criteria],
        reasons=result.reasons,
        drop_reason=result.drop_reason,
        model=getattr(scorer, "model_id", ""),
    )
    return ScoreOutcome(candidate_id=candidate_id, result=result)
