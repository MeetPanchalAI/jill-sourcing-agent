"""Ingest stage: detect recent joiners from a company scrape and persist them as
candidates + provenance edges, idempotently.

This is plain orchestration over an idempotent web-py client — re-running it on
the same scrape upserts the same candidate rows and matches (rather than
duplicates) the same ``recent_joiner`` edges. Determinism for the workflow layer
comes from passing ``as_of`` in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..brightdata.types import EmployeeRef
from ..detect import DetectionResult, detect_recent_joiners


@dataclass
class IngestSummary:
    company: str
    detection: DetectionResult
    candidate_ids: list[int]
    edges_created: int
    # {id, linkedin_url} for each recent joiner — the frontier the workflow
    # enqueues for evaluation.
    leads: list[dict]

    @property
    def candidates_seen(self) -> int:
        return len(self.candidate_ids)


def ingest_recent_joiners(
    client,
    *,
    role_id: int,
    run_id: int | None,
    company: str,
    from_company_id: int | None,
    employees: list[EmployeeRef],
    as_of: date,
    window_days: int,
    depth: int = 0,
) -> IngestSummary:
    """Persist the recent joiners among ``employees`` for ``role_id``.

    Returns a summary including how many edges were *newly* created (so callers
    can update run counters without double-counting on a re-run)."""
    detection = detect_recent_joiners(employees, as_of, window_days)

    candidate_ids: list[int] = []
    leads: list[dict] = []
    edges_created = 0
    for emp in detection.recent:
        cand = client.upsert_candidate(
            linkedin_url=emp.linkedin_url,
            full_name=emp.full_name,
            headline=emp.headline,
            current_company=emp.current_company,
            current_title=emp.current_title,
            location=emp.location,
            started_current_role_at=emp.started_at,
            first_seen_run=run_id,
        )
        candidate_ids.append(cand.id)
        leads.append({"id": cand.id, "linkedin_url": emp.linkedin_url})
        edge = client.create_lead_edge(
            role=role_id,
            run=run_id,
            to_candidate=cand.id,
            kind="recent_joiner",
            from_company=from_company_id,
            depth=depth,
        )
        if edge.created:
            edges_created += 1

    return IngestSummary(
        company=company,
        detection=detection,
        candidate_ids=candidate_ids,
        edges_created=edges_created,
        leads=leads,
    )
