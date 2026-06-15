"""P5 gate (tests.md §3): lead-source fan-out — prev-employer + network edges,
bounded by depth and budget, with a reconstructable provenance chain."""

from __future__ import annotations

from datetime import date

from jill.brightdata.mock import MockBrightdataClient
from jill.pipeline.expand import Budget, expand_lead
from jill.pipeline.ingest import ingest_recent_joiners
from jill.webpy.fake import FakeWebPy

BD = MockBrightdataClient()
AS_OF = date(2026, 6, 14)
WINDOW = 90


def _budget(max_leads=50, max_scrapes=100) -> Budget:
    return Budget(max_leads=max_leads, max_scrapes=max_scrapes)


def _alice():
    return BD.profile("https://linkedin.com/in/alice-nguyen")


# --- T3.1 provenance edges ---------------------------------------------------


def test_expand_creates_prev_employer_targets_and_network_edges():
    client = FakeWebPy()
    res = expand_lead(
        client, BD, role_id=1, run_id=5, lead_candidate_id=99,
        lead_profile=_alice(), depth=0, max_depth=2, budget=_budget(),
    )
    assert "Retell AI" in res.prev_employer_companies
    assert "Twilio" in res.prev_employer_companies
    # prev-employer targets are tagged with the lead that surfaced them.
    retell = next(t for t in client.targets if t["name"] == "Retell AI")
    assert retell["source"] == "prev_employer"
    assert retell["depth"] == 1
    assert retell["discovered_from"] == 99
    # network produced candidates + network edges from the lead.
    assert res.network_candidate_ids
    assert res.network_edges_created == len(res.network_candidate_ids)
    net_edges = [e for e in client.edges.values() if e["kind"] == "network"]
    assert all(e["from_candidate"] == 99 for e in net_edges)
    assert all(e["method"] == "shared_company" for e in net_edges)
    assert all(e["depth"] == 1 for e in net_edges)


# --- T3.2 depth bound --------------------------------------------------------


def test_no_expansion_at_max_depth():
    client = FakeWebPy()
    res = expand_lead(
        client, BD, role_id=1, run_id=5, lead_candidate_id=99,
        lead_profile=_alice(), depth=2, max_depth=2, budget=_budget(),
    )
    assert res.skipped_depth
    assert not res.expanded
    assert client.targets == []
    assert client.edges == {}


# --- T3.3 budget bound -------------------------------------------------------


def test_network_respects_lead_budget():
    client = FakeWebPy()
    # Retell cohort for Alice = Frank + Grace (2 peers); cap at 1.
    res = expand_lead(
        client, BD, role_id=1, run_id=5, lead_candidate_id=99,
        lead_profile=_alice(), depth=0, max_depth=2,
        budget=_budget(max_leads=1, max_scrapes=100),
    )
    assert len(res.network_candidate_ids) == 1
    assert res.budget_reached


def test_scrape_budget_blocks_network_but_not_prev_employer():
    client = FakeWebPy()
    res = expand_lead(
        client, BD, role_id=1, run_id=5, lead_candidate_id=99,
        lead_profile=_alice(), depth=0, max_depth=2,
        budget=_budget(max_leads=50, max_scrapes=0),
    )
    assert res.network_candidate_ids == []
    assert "Retell AI" in res.prev_employer_companies  # no scrape needed


# --- T3.4 full provenance chain (seed → prev employer → its joiner) ----------


def test_provenance_chain_reconstructs_end_to_end():
    client = FakeWebPy()

    # 1. Seed scan: Vapi recent joiners (depth 0).
    ingest_recent_joiners(
        client, role_id=1, run_id=5, company="Vapi", from_company_id=None,
        employees=BD.company_employees("Vapi"), as_of=AS_OF, window_days=WINDOW,
        depth=0,
    )
    alice_id = client.candidates["https://linkedin.com/in/alice-nguyen"]["id"]

    # 2. Alice is a fit → expand (depth 0 → 1): surfaces Retell + network.
    expand_lead(
        client, BD, role_id=1, run_id=5, lead_candidate_id=alice_id,
        lead_profile=_alice(), depth=0, max_depth=2, budget=_budget(),
    )
    retell = next(t for t in client.targets if t["name"] == "Retell AI")
    assert retell["discovered_from"] == alice_id

    # 3. Scan the discovered prev-employer (depth 1): Frank is a recent joiner.
    ingest_recent_joiners(
        client, role_id=1, run_id=5, company="Retell AI",
        from_company_id=retell["id"],
        employees=BD.company_employees("Retell AI"), as_of=AS_OF,
        window_days=WINDOW, depth=1,
    )
    frank_id = client.candidates["https://linkedin.com/in/frank-li"]["id"]

    # Frank is reachable via two provenance edges:
    frank_edges = [e for e in client.edges.values() if e["to_candidate"] == frank_id]
    kinds = {e["kind"] for e in frank_edges}
    assert "network" in kinds        # Alice's shared-company cohort
    assert "recent_joiner" in kinds  # joiner at Retell (Alice's ex-employer)
    # Chain: Vapi → Alice (recent_joiner d0) → Retell (discovered_from Alice)
    #        → Frank (recent_joiner d1). Depth never exceeds max.
    assert all(e["depth"] <= 2 for e in client.edges.values())
