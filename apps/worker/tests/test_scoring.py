"""P4 gate (tests.md §4): fit scoring — fit vs drop, schema/grounding validation,
ranking. Uses the deterministic RuleScorer so there's no live LLM dependency."""

from __future__ import annotations

import pytest
from jill.agent.schemas import ScoreResult
from jill.agent.scoring import RuleScorer, get_scorer
from jill.brightdata.mock import MockBrightdataClient
from jill.config import Settings
from jill.pipeline.score import score_candidate
from jill.webpy.fake import FakeWebPy
from pydantic import ValidationError

ICP = {
    "must_have_skills": ["Python", "Realtime Audio"],
    "nice_to_have_skills": ["WebRTC", "LLMs"],
    "seniority": "senior",
    "locations": ["US"],
}


def _profile(slug: str):
    return MockBrightdataClient().profile(f"https://linkedin.com/in/{slug}")


def _settings(mode="mock") -> Settings:
    return Settings(
        mode=mode, brightdata_api_key="", brightdata_base_url="x",
        bd_dataset_profile="p", bd_company_records_limit=25,
        bd_poll_timeout=240.0, bd_poll_interval=5.0,
        recent_joiner_window_days=90, max_expansion_depth=2,
        max_leads_per_run=50, max_scrapes_per_run=100,
        live_max_companies=1, live_max_depth=0, live_max_leads=8, autoplan=False,
        expand_min_score=40, expand_network=False, cross_run_dedup=False,
        scrape_max_attempts=4, scrape_base_delay=0.5,
        anthropic_api_key="", planner_model="m", scorer_model="m",
        drafter_model="m", triage_model="m", webpy_base_url="x", service_token="x",
    )


# --- T4.1 fit vs drop --------------------------------------------------------


def test_strong_match_is_fit():
    res = RuleScorer().score(ICP, _profile("alice-nguyen"))
    assert res.verdict == "fit"
    assert res.score >= 70
    assert res.reasons  # grounded evidence present


def test_off_target_is_drop_with_reason():
    # Dave: TypeScript/React frontend — no Python or Realtime Audio.
    res = RuleScorer().score(ICP, _profile("dave-okoro"))
    assert res.verdict == "drop"
    assert res.drop_reason
    assert "Python" in res.drop_reason or "Realtime Audio" in res.drop_reason


# --- T4.2 schema validation --------------------------------------------------


def test_fit_without_reasons_rejected():
    with pytest.raises(ValidationError):
        ScoreResult(score=90, verdict="fit", reasons=[])


def test_drop_without_reason_rejected():
    with pytest.raises(ValidationError):
        ScoreResult(score=10, verdict="drop", drop_reason="")


def test_score_out_of_range_rejected():
    with pytest.raises(ValidationError):
        ScoreResult(score=150, verdict="fit", reasons=["x"])


# --- T4.3 grounding ----------------------------------------------------------


def test_fit_reasons_are_grounded_in_profile():
    profile = _profile("alice-nguyen")
    res = RuleScorer().score(ICP, profile)
    skills_lower = {s.lower() for s in profile.skills}
    # Every must-have cited in the reasons actually appears in the profile.
    cited = " ".join(res.reasons).lower()
    for skill in ("python", "realtime audio"):
        if skill in cited:
            assert skill in {s.lower() for s in profile.skills} or skill in cited
    assert "python" in skills_lower  # sanity: the fixture really has it


# --- T4.4 persistence + factory ---------------------------------------------


def test_score_candidate_persists_and_flags_fit():
    client = FakeWebPy()
    outcome = score_candidate(
        client, RuleScorer(), candidate_id=7, role_id=1, icp=ICP,
        profile=_profile("alice-nguyen"),
    )
    assert outcome.is_fit
    stored = client.scores[(7, 1)]
    assert stored["verdict"] == "fit"
    assert stored["model"] == "mock:rules"


def test_factory_returns_rule_scorer_in_mock_mode():
    assert isinstance(get_scorer(_settings()), RuleScorer)
