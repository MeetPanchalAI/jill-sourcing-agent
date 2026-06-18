"""Expand stage: the "lead sources" fan-out.

For a *fit* lead, grow the graph along two edges, bounded by depth and budget:

  * **previous employer** → a new ``TargetCompany(source=prev_employer)`` to
    monitor, tagged with the lead that surfaced it (``discovered_from``).
  * **network** → new ``Candidate`` rows linked back to the lead with a
    ``LeadEdge(kind=network)``, approximated via shared-company cohort.

Expansion stops once ``depth`` reaches ``max_depth`` (C11), and the per-run
``Budget`` caps how many network leads and scrapes a single run may spend.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..brightdata.base import BrightdataClient
from ..brightdata.errors import BrightdataError
from ..brightdata.types import Profile


@dataclass
class Budget:
    """Per-run work cap. The orchestrator builds one from settings and threads it
    through every expansion so the whole crawl stays finite."""

    max_leads: int
    max_scrapes: int
    leads_used: int = 0
    scrapes_used: int = 0

    def can_lead(self) -> bool:
        return self.leads_used < self.max_leads

    def can_scrape(self) -> bool:
        return self.scrapes_used < self.max_scrapes

    def take_lead(self) -> None:
        self.leads_used += 1

    def take_scrape(self) -> None:
        self.scrapes_used += 1


@dataclass
class ExpandResult:
    prev_employer_companies: list[str]
    network_candidate_ids: list[int]
    network_edges_created: int
    budget_reached: bool
    skipped_depth: bool
    # {id, linkedin_url} per network lead — the frontier the workflow enqueues.
    network_leads: list[dict] = field(default_factory=list)

    @property
    def expanded(self) -> bool:
        return not self.skipped_depth


def expand_lead(
    client,
    brightdata: BrightdataClient,
    *,
    role_id: int,
    run_id: int | None,
    lead_candidate_id: int,
    lead_profile: Profile,
    depth: int,
    max_depth: int,
    budget: Budget,
) -> ExpandResult:
    result = ExpandResult([], [], 0, budget_reached=False, skipped_depth=False,
                          network_leads=[])

    if depth >= max_depth:
        result.skipped_depth = True
        return result

    child_depth = depth + 1

    # --- previous employers → new company targets ---
    for company in lead_profile.previous_companies():
        client.create_target(
            role=role_id,
            name=company,
            source="prev_employer",
            depth=child_depth,
            discovered_from=lead_candidate_id,
        )
        result.prev_employer_companies.append(company)

    # --- network → new candidate leads ---
    if budget.can_scrape():
        budget.take_scrape()
        try:
            peers = brightdata.network(lead_profile)
        except BrightdataError:
            peers = []
        for peer in peers:
            if not budget.can_lead():
                result.budget_reached = True
                break
            budget.take_lead()
            cand = client.upsert_candidate(
                linkedin_url=peer.linkedin_url,
                full_name=peer.full_name,
                headline=peer.headline,
                current_company=peer.current_company,
                current_title=peer.current_title,
                location=peer.location,
                started_current_role_at=peer.started_at,
                first_seen_run=run_id,
            )
            result.network_candidate_ids.append(cand.id)
            result.network_leads.append(
                {"id": cand.id, "linkedin_url": peer.linkedin_url}
            )
            edge = client.create_lead_edge(
                role=role_id,
                run=run_id,
                to_candidate=cand.id,
                kind="network",
                from_candidate=lead_candidate_id,
                depth=child_depth,
                method=getattr(brightdata, "network_method", "network"),
            )
            if edge.created:
                result.network_edges_created += 1

    return result
