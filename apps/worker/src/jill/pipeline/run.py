"""In-process sourcing runner — the same frontier-BFS as the Temporal workflow,
executed inline.

This is the dev/demo escape hatch: ``jill source --local`` and the e2e smoke test
use it to run the full monitor → enrich → score → expand → draft pipeline without
a Temporal server. The durable production path is the workflow; this mirrors its
logic against injected clients.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, date, datetime

from ..agent.scoring import Scorer
from ..brightdata.base import BrightdataClient
from ..brightdata.errors import BrightdataNotFound
from .evaluate import evaluate_lead
from .expand import Budget
from .ingest import ingest_recent_joiners


@dataclass
class RunResult:
    status: str
    scanned: int = 0
    found: int = 0
    evaluated: int = 0
    fit: int = 0
    drafted: int = 0


def _report(client, run_id: int, status: str, r: RunResult) -> None:
    """Push the run's current status + counters to web-py (progress + finalize)."""
    client.finalize_run(
        run_id, status=status, scanned_companies=r.scanned,
        found_candidates=r.found, fit_candidates=r.fit, drafted=r.drafted,
        budget_used=r.evaluated,
    )


def run_sourcing(
    client,
    brightdata: BrightdataClient,
    scorer: Scorer,
    drafter,
    *,
    role_id: int,
    run_id: int,
    role_title: str,
    icp: dict,
    seed_companies: list[str],
    as_of: date | None = None,
    max_depth: int = 2,
    window_days: int = 90,
    max_leads: int = 50,
    max_companies: int = 50,
) -> RunResult:
    as_of = as_of or datetime.now(UTC).date()
    companies: deque = deque((c, 0) for c in seed_companies)
    candidates: deque = deque()
    seen_companies: set[str] = set()
    seen_candidates: set[int] = set()
    r = RunResult(status="running")
    budget_hit = False

    while companies or candidates:
        if companies:
            company, depth = companies.popleft()
            key = company.strip().lower()
            if key in seen_companies:
                continue
            seen_companies.add(key)
            if r.scanned >= max_companies:
                budget_hit = True
                continue
            target = client.create_target(
                role=role_id, name=company,
                source="seed" if depth == 0 else "prev_employer", depth=depth,
            )
            try:
                employees = brightdata.company_employees(company)
            except BrightdataNotFound:
                r.scanned += 1
                continue
            summary = ingest_recent_joiners(
                client, role_id=role_id, run_id=run_id, company=company,
                from_company_id=target.id, employees=employees, as_of=as_of,
                window_days=window_days, depth=depth,
            )
            r.scanned += 1
            r.found += summary.edges_created
            for lead in summary.leads:
                candidates.append((lead["id"], lead["linkedin_url"], depth))
            _report(client, run_id, "running", r)  # live progress for the dashboard
            continue

        cid, url, depth = candidates.popleft()
        if cid in seen_candidates:
            continue
        seen_candidates.add(cid)
        if r.evaluated >= max_leads:
            budget_hit = True
            continue
        out = evaluate_lead(
            client, brightdata, scorer, drafter, role_id=role_id, run_id=run_id,
            candidate_id=cid, linkedin_url=url, role_title=role_title, icp=icp,
            depth=depth, max_depth=max_depth,
            budget=Budget(max_leads=max_leads, max_scrapes=max_companies * 5),
            fetched_at=datetime.now(UTC).isoformat(),
        )
        r.evaluated += 1
        if out.is_fit:
            r.fit += 1
            r.drafted += out.drafted
            for comp in out.prev_employer_companies:
                companies.append((comp, depth + 1))
            for lead in out.network_leads:
                candidates.append((lead["id"], lead["linkedin_url"], depth + 1))

    r.status = "budget_exhausted" if budget_hit else "completed"
    _report(client, run_id, r.status, r)
    return r
