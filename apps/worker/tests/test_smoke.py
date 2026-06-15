"""P8 gate (tests.md T9.1): the end-to-end demo. Seed one company and assert the
whole pipeline yields de-duplicated fit leads with scorecards, provenance to
depth >= 2, and queued drafts — deterministic, fully in-process (mock everything).
"""

from __future__ import annotations

from datetime import date

from jill.agent.drafting import TemplateDrafter
from jill.agent.scoring import RuleScorer
from jill.brightdata.mock import MockBrightdataClient
from jill.pipeline.run import run_sourcing
from jill.webpy.fake import FakeWebPy

ICP = {
    "must_have_skills": ["Python", "Realtime Audio"],
    "nice_to_have_skills": ["WebRTC", "LLMs"],
}


def _run():
    client = FakeWebPy()
    result = run_sourcing(
        client, MockBrightdataClient(), RuleScorer(), TemplateDrafter(),
        role_id=1, run_id=100, role_title="Voice AI Engineer", icp=ICP,
        seed_companies=["Vapi"], as_of=date(2026, 6, 14),
    )
    return client, result


def test_demo_yields_fit_leads_with_drafts():
    client, r = _run()
    assert r.status == "completed"
    assert r.fit >= 2                 # ≥2 de-duplicated fit leads
    assert r.drafted == r.fit * 2     # linkedin + email per fit, idempotent
    # every fit candidate has a persisted scorecard
    fit_scores = [s for s in client.scores.values() if s["verdict"] == "fit"]
    assert len(fit_scores) == r.fit


def test_demo_fan_out_reaches_depth_2():
    client, _ = _run()
    # seed Vapi (d0) → joiner Alice (d0) → her ex-employer Retell (d1)
    # → Retell joiner Frank (d1) → Frank's ex-employer Deepgram (d2)
    retell = next(t for t in client.targets if t["name"] == "Retell AI")
    assert retell["source"] == "prev_employer"
    assert max(t["depth"] for t in client.targets) >= 2


def test_demo_leads_have_provenance_and_are_deduped():
    client, _ = _run()
    # Frank is discovered twice (network cohort + Retell joiner) but deduped.
    frank = client.candidates["https://linkedin.com/in/frank-li"]
    frank_edges = [e for e in client.edges.values()
                   if e["to_candidate"] == frank["id"]]
    assert {e["kind"] for e in frank_edges} >= {"network", "recent_joiner"}


def test_demo_is_idempotent_on_rerun():
    client = FakeWebPy()
    args = dict(role_id=1, run_id=100, role_title="Voice AI Engineer", icp=ICP,
                seed_companies=["Vapi"], as_of=date(2026, 6, 14))
    bd, scorer, drafter = MockBrightdataClient(), RuleScorer(), TemplateDrafter()
    first = run_sourcing(client, bd, scorer, drafter, **args)
    candidates_after_first = len(client.candidates)
    edges_after_first = len(client.edges)
    second = run_sourcing(client, bd, scorer, drafter, **args)
    assert (second.fit, second.drafted) == (first.fit, first.drafted)
    assert len(client.candidates) == candidates_after_first  # no dupes
    assert len(client.edges) == edges_after_first
