"""Structured outputs for Jill's judgment calls — schema-validated (C14, C16).

The cross-field validators enforce grounding at the type level: a ``fit`` must
cite at least one reason, a ``drop`` must say what's missing. Malformed LLM
output fails validation and is retried/rejected rather than persisted.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CriterionResult(BaseModel):
    """How a single rubric criterion scored for a candidate."""

    name: str
    weight: float = 1.0
    status: Literal["met", "partial", "missed"]
    detail: str = ""  # grounded evidence, e.g. "IIT Bombay, 2014"


class ScoreResult(BaseModel):
    score: int = Field(ge=0, le=100, description="0-100 fit score")
    verdict: Literal["fit", "drop"]
    summary: str = Field(
        default="", description="One crisp line a recruiter can skim."
    )
    criteria: list[CriterionResult] = Field(
        default_factory=list, description="Per-rubric-criterion breakdown."
    )
    reasons: list[str] = Field(
        default_factory=list,
        description="Evidence from the candidate's profile supporting the verdict.",
    )
    drop_reason: str = Field(
        default="", description="Why the candidate was dropped (required for drop)."
    )

    @model_validator(mode="after")
    def _grounded(self) -> ScoreResult:
        if self.verdict == "fit" and not (self.reasons or self.criteria):
            raise ValueError("a 'fit' verdict must cite reasons or criteria")
        if self.verdict == "drop" and not (self.drop_reason or self.criteria):
            raise ValueError("a 'drop' verdict must give a drop_reason or criteria")
        return self


class OutreachResult(BaseModel):
    """Produced by the drafter (P6)."""

    subject: str = Field(default="", description="Email subject (empty for LinkedIn).")
    body: str = Field(min_length=1, description="The personalized invite body.")
