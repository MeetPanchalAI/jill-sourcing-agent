"""Claude-backed judgment (live mode). Uses the Anthropic SDK's structured
``messages.parse`` so output is schema-validated; retry once on malformed output,
then fail the activity (C14). Not exercised by the test suite — engages only when
``ANTHROPIC_API_KEY`` is set."""

from __future__ import annotations

import logging

import anthropic
from pydantic import ValidationError

from ..brightdata.types import Profile
from ..config import Settings
from .prompts import (
    DRAFT_SYSTEM,
    PLAN_SYSTEM,
    SCORE_SYSTEM,
    build_draft_user,
    build_plan_user,
    build_score_user,
)
from .schemas import OutreachResult, ScoreResult, SeedPlan

logger = logging.getLogger("jill.agent")


def _parse_retry(client, *, model, system, user, output_format, max_tokens=1024):
    """Call messages.parse with one retry on malformed/invalid output (C14)."""
    last: Exception | None = None
    for attempt in range(2):
        try:
            resp = client.messages.parse(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=output_format,
            )
            if resp.parsed_output is not None:
                return resp.parsed_output
            last = RuntimeError("empty parsed_output")
        except ValidationError as exc:
            last = exc
            logger.warning("%s output invalid (attempt %d): %s",
                           output_format.__name__, attempt + 1, exc)
    raise RuntimeError(f"{output_format.__name__} failed after retries: {last}") \
        from last


class ClaudePlanner:
    """Proposes seed companies for a role from its title + ICP (P1)."""

    def __init__(self, settings: Settings):
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY required for live planning")
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model_id = settings.planner_model

    def propose_seeds(self, role_title: str, icp: dict, n: int = 3,
                      exclude: list[str] | None = None) -> SeedPlan:
        return _parse_retry(
            self._client, model=self.model_id, system=PLAN_SYSTEM,
            user=build_plan_user(role_title, icp, n, exclude), output_format=SeedPlan,
        )


class ClaudeScorer:
    def __init__(self, settings: Settings):
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY required for live scoring")
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model_id = settings.scorer_model

    def score(self, icp: dict, profile: Profile) -> ScoreResult:
        return _parse_retry(
            self._client, model=self.model_id, system=SCORE_SYSTEM,
            user=build_score_user(icp, profile), output_format=ScoreResult,
        )


class ClaudeDrafter:
    def __init__(self, settings: Settings):
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY required for live drafting")
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model_id = settings.drafter_model

    def draft(self, ctx, channel: str) -> OutreachResult:
        from dataclasses import asdict

        return _parse_retry(
            self._client, model=self.model_id, system=DRAFT_SYSTEM,
            user=build_draft_user(asdict(ctx), channel),
            output_format=OutreachResult,
        )
