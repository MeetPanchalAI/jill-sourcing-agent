"""P7 activity-layer tests: the IO activities wire the pipeline stages to the
clients correctly. Run directly (no Temporal) with the mock Brightdata client and
FakeWebPy patched in — proves the activities' side effects without a server."""

from __future__ import annotations

import jill.workflows.activities as acts
from jill.brightdata.mock import MockBrightdataClient
from jill.webpy.fake import FakeWebPy
from jill.workflows.types import EvalArgs, FinalizeArgs, ScanArgs

ICP = {"must_have_skills": ["Python", "Realtime Audio"], "nice_to_have_skills": []}
ALICE = "https://linkedin.com/in/alice-nguyen"


def _patch(monkeypatch, fake):
    monkeypatch.setattr(acts, "get_client", lambda s=None: MockBrightdataClient())
    monkeypatch.setattr(acts, "get_webpy_client", lambda tenant, s=None: fake)


def test_scan_company_ingests_recent_joiners(monkeypatch):
    fake = FakeWebPy()
    _patch(monkeypatch, fake)
    res = acts.scan_company(
        ScanArgs(role_id=1, run_id=10, tenant_id=1, company="Vapi", depth=0,
                 as_of="2026-06-14", window_days=90)
    )
    assert res.new_candidates == 3  # alice, bob, dave
    assert len(res.leads) == 3
    assert any(t["name"] == "Vapi" and t["source"] == "seed" for t in fake.targets)


def test_evaluate_candidate_enriches_scores_drafts_expands(monkeypatch):
    fake = FakeWebPy()
    _patch(monkeypatch, fake)
    # seed Alice as a candidate
    alice_id = fake.upsert_candidate(linkedin_url=ALICE, full_name="Alice").id
    res = acts.evaluate_candidate(
        EvalArgs(role_id=1, run_id=10, tenant_id=1, candidate_id=alice_id,
                 linkedin_url=ALICE, role_title="Voice AI Engineer", icp=ICP,
                 depth=0, max_depth=2)
    )
    assert res.is_fit
    assert res.drafted == 2  # linkedin + email
    assert "Retell AI" in res.prev_employer_companies
    assert res.network_leads  # shared-company cohort
    # side effects landed
    assert alice_id in fake.enrichments
    assert (alice_id, 1) in fake.scores
    assert fake.scores[(alice_id, 1)]["verdict"] == "fit"


def test_evaluate_skips_when_profile_missing(monkeypatch):
    fake = FakeWebPy()
    _patch(monkeypatch, fake)
    res = acts.evaluate_candidate(
        EvalArgs(role_id=1, run_id=10, tenant_id=1, candidate_id=5,
                 linkedin_url="https://linkedin.com/in/ghost", role_title="x",
                 icp=ICP, depth=0, max_depth=2)
    )
    assert res.skipped and not res.is_fit


def test_finalize_run_writes_status_and_counters(monkeypatch):
    fake = FakeWebPy()
    _patch(monkeypatch, fake)
    acts.finalize_run(FinalizeArgs(
        run_id=10, tenant_id=1, status="completed",
        counters={"scanned": 2, "found": 3, "fit": 1, "drafted": 2,
                  "budget_used": 4},
    ))
    assert fake.runs[10]["status"] == "completed"
    assert fake.runs[10]["fit_candidates"] == 1
