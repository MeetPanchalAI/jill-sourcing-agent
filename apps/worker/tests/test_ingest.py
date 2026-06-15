"""P3 gate (tests.md §3/§7): ingesting recent joiners is idempotent — re-running
the same scrape upserts the same candidates and matches the same edges, never
duplicating."""

from __future__ import annotations

from datetime import date

from jill.brightdata.mock import MockBrightdataClient
from jill.pipeline.ingest import ingest_recent_joiners
from jill.webpy.fake import FakeWebPy

AS_OF = date(2026, 6, 14)
WINDOW = 90


def _run(client):
    employees = MockBrightdataClient().company_employees("Vapi")
    return ingest_recent_joiners(
        client, role_id=1, run_id=10, company="Vapi", from_company_id=5,
        employees=employees, as_of=AS_OF, window_days=WINDOW, depth=0,
    )


def test_ingest_creates_candidates_and_edges():
    client = FakeWebPy()
    summary = _run(client)
    # Vapi fixture: alice/bob/dave recent, carol old, erin no-date → 3 recent.
    assert summary.detection.recent_count == 3
    assert summary.candidates_seen == 3
    assert summary.edges_created == 3
    assert len(client.candidates) == 3
    # Every edge is a recent_joiner edge from the origin company.
    assert all(e["kind"] == "recent_joiner" for e in client.edges.values())
    assert all(e["from_company"] == 5 for e in client.edges.values())


def test_ingest_is_idempotent_on_rerun():
    client = FakeWebPy()
    first = _run(client)
    second = _run(client)
    # Second run creates nothing new.
    assert first.edges_created == 3
    assert second.edges_created == 0
    assert len(client.candidates) == 3
    assert len(client.edges) == 3


def test_started_at_propagated_to_candidate():
    client = FakeWebPy()
    _run(client)
    alice = client.candidates["https://linkedin.com/in/alice-nguyen"]
    assert alice["started_current_role_at"] == "2026-05-15"
    assert alice["first_seen_run"] == 10
