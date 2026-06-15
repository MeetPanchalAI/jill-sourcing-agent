"""Rubric scoring: per-criterion met/partial/missed with grounded evidence, the
weighted score, and a crisp summary. Deterministic (fixed as_of)."""

from __future__ import annotations

from datetime import date

from jill.agent.rubric import evaluate_rubric
from jill.agent.scoring import RuleScorer
from jill.brightdata.mock import MockBrightdataClient

BD = MockBrightdataClient()
AS_OF = date(2026, 6, 14)

RUBRIC = [
    {"name": "Ex-founder", "type": "founder", "weight": 2},
    {"name": "Pedigree", "type": "pedigree",
     "schools": ["IIT", "NIT", "BITS", "Stanford", "MIT", "CMU", "Berkeley"],
     "weight": 2},
    {"name": "Python", "type": "skill", "skill": "Python", "weight": 1},
    {"name": "Voice domain", "type": "domain",
     "keywords": ["voice", "speech", "audio", "realtime"], "weight": 2},
    {"name": "Tenure 2-6y", "type": "tenure", "min_years": 2, "max_years": 6,
     "weight": 1},
    {"name": "0-to-1 builder", "type": "open", "description": "early", "weight": 1},
]


def _profile(slug):
    return BD.profile(f"https://linkedin.com/in/{slug}")


def _by_name(criteria):
    return {c.name: c for c in criteria}


def test_ex_founder_detected_for_alice():
    crit, score, _summary = evaluate_rubric(RUBRIC, _profile("alice-nguyen"), AS_OF)
    c = _by_name(crit)
    assert c["Ex-founder"].status == "met"          # Co-founder & CTO at EchoLabs
    assert c["Pedigree"].status == "met"            # UC Berkeley
    assert "Berkeley" in c["Pedigree"].detail
    assert c["Python"].status == "met"
    assert c["Voice domain"].status == "met"
    assert score >= 75


def test_iit_pedigree_for_bob():
    crit, _score, _ = evaluate_rubric(RUBRIC, _profile("bob-martinez"), AS_OF)
    c = _by_name(crit)
    assert c["Pedigree"].status == "met"
    assert "IIT" in c["Pedigree"].detail              # grounded in real education
    assert c["Ex-founder"].status == "missed"
    assert c["Tenure 2-6y"].status == "met"           # ~4y career


def test_off_target_dave_scores_low():
    crit, score, _ = evaluate_rubric(RUBRIC, _profile("dave-okoro"), AS_OF)
    c = _by_name(crit)
    assert c["Python"].status == "missed"             # TypeScript/React
    assert c["Voice domain"].status == "missed"
    assert c["Pedigree"].status == "missed"           # NYU not elite
    assert score < 40


def test_summary_lists_met_criteria():
    _, _, summary = evaluate_rubric(RUBRIC, _profile("alice-nguyen"), AS_OF)
    assert "Ex-founder" in summary
    assert "criteria met" in summary


def test_tenure_band_discriminates():
    # Bob (~4y) is inside the 2-6y band; Alice (~8y, founder since 2018) is well
    # outside it → missed. The band genuinely discriminates.
    alice = _by_name(evaluate_rubric(RUBRIC, _profile("alice-nguyen"), AS_OF)[0])
    bob = _by_name(evaluate_rubric(RUBRIC, _profile("bob-martinez"), AS_OF)[0])
    assert alice["Tenure 2-6y"].status == "missed"
    assert bob["Tenure 2-6y"].status == "met"


def test_rule_scorer_uses_rubric_when_present():
    res = RuleScorer().score({"rubric": RUBRIC}, _profile("alice-nguyen"))
    assert res.verdict == "fit"
    assert res.summary
    assert res.criteria  # structured breakdown carried through
    drop = RuleScorer().score({"rubric": RUBRIC}, _profile("dave-okoro"))
    assert drop.verdict == "drop"
    assert drop.drop_reason


def test_rule_scorer_falls_back_to_skills_without_rubric():
    # Legacy path still works (no rubric key).
    res = RuleScorer().score(
        {"must_have_skills": ["Python", "Realtime Audio"]}, _profile("alice-nguyen")
    )
    assert res.verdict == "fit"
    assert not res.criteria  # legacy path doesn't populate the rubric breakdown
