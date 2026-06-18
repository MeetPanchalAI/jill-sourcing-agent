"""Fit scoring — the first judgment point.

``Scorer`` is the interface; ``RuleScorer`` is the deterministic mock (the test
default), and ``get_scorer`` returns the Claude-backed scorer in live mode. The
mock grounds its reasons by construction (it only cites skills it actually
matched), so the grounding contract holds without an LLM.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..brightdata.types import Profile
from ..config import Settings, get_settings
from .rubric import evaluate_rubric
from .schemas import ScoreResult


@runtime_checkable
class Scorer(Protocol):
    model_id: str

    def score(self, icp: dict, profile: Profile) -> ScoreResult: ...


def _blob(profile: Profile) -> str:
    parts = [
        *profile.skills,
        profile.current_title,
        profile.headline,
        profile.about,
        *[e.title for e in profile.experiences],
        *[e.company for e in profile.experiences],
    ]
    return " ".join(p for p in parts if p).lower()


class RuleScorer:
    """Deterministic scorer (the mock / test default).

    Two paths: when the ICP carries a ``rubric`` it delegates to the weighted
    per-criterion evaluator; otherwise it falls back to keyword overlap on the
    must/nice-have skills. A candidate is a fit when at least ``fit_threshold``
    of the signal is met (half the must-have skills, or a score of 50/100).
    """

    model_id = "mock:rules"

    def __init__(self, fit_threshold: float = 0.5):
        self.fit_threshold = fit_threshold

    def score(self, icp: dict, profile: Profile) -> ScoreResult:
        rubric = icp.get("rubric")
        if rubric:
            return self._score_rubric(rubric, profile)
        return self._score_skills(icp, profile)

    def _score_rubric(self, rubric: list[dict], profile: Profile) -> ScoreResult:
        criteria, score, summary = evaluate_rubric(rubric, profile)
        fit = score >= self.fit_threshold * 100
        reasons = [f"{c.name}: {c.detail}" for c in criteria if c.status == "met"]
        missed = [c.name for c in criteria if c.status == "missed"]
        if fit:
            return ScoreResult(
                score=score, verdict="fit", summary=summary, criteria=criteria,
                reasons=reasons or [summary],
            )
        drop_reason = "Missing: " + ", ".join(missed) if missed else summary
        return ScoreResult(
            score=score, verdict="drop", summary=summary, criteria=criteria,
            drop_reason=drop_reason,
        )

    def _score_skills(self, icp: dict, profile: Profile) -> ScoreResult:
        # Legacy path: keyword overlap on must/nice-have skills (no rubric).
        # Keep original casing for human-readable reasons; match case-insensitively.
        must = list(icp.get("must_have_skills", []))
        nice = list(icp.get("nice_to_have_skills", []))
        blob = _blob(profile)

        matched_must = [m for m in must if m.lower() in blob]
        matched_nice = [n for n in nice if n.lower() in blob]
        must_cov = len(matched_must) / len(must) if must else 1.0
        nice_cov = len(matched_nice) / len(nice) if nice else 0.0
        score = round(100 * (0.75 * must_cov + 0.25 * nice_cov))

        is_fit = (must_cov >= self.fit_threshold) if must else (score > 0)
        if is_fit:
            reasons: list[str] = []
            if matched_must:
                reasons.append("Matches must-have skills: " + ", ".join(matched_must))
            if matched_nice:
                reasons.append("Also has: " + ", ".join(matched_nice))
            if not reasons:
                reasons.append(
                    f"Current role '{profile.current_title}' aligns with the ICP"
                )
            return ScoreResult(score=score, verdict="fit", reasons=reasons)

        missing = [m for m in must if m not in matched_must]
        drop_reason = (
            "Missing must-have skills: " + ", ".join(missing)
            if missing
            else "Insufficient overlap with the ICP"
        )
        return ScoreResult(
            score=score, verdict="drop", reasons=[], drop_reason=drop_reason
        )


def get_scorer(settings: Settings | None = None) -> Scorer:
    settings = settings or get_settings()
    if settings.is_live:
        from .claude import ClaudeScorer  # lazy: avoid anthropic import in mock paths

        return ClaudeScorer(settings)
    return RuleScorer()
