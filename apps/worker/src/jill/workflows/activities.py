"""Activities — the IO boundary. Each wraps a pipeline stage with the live
clients (Brightdata, web-py, scorer, drafter). Sync functions (Temporal runs them
in a thread pool); idempotent via the upserting web-py client, so retries and
re-runs are safe (C10).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from temporalio import activity

from ..agent.drafting import get_drafter
from ..agent.scoring import get_scorer
from ..brightdata import get_client
from ..brightdata.errors import BrightdataNotFound
from ..config import get_settings
from ..pipeline.evaluate import evaluate_lead
from ..pipeline.expand import Budget
from ..pipeline.ingest import ingest_recent_joiners
from ..webpy import get_webpy_client
from .types import EvalArgs, EvalResult, FinalizeArgs, ScanArgs, ScanResult


@activity.defn(name="scan_company")
def scan_company(a: ScanArgs) -> ScanResult:
    s = get_settings()
    bd = get_client(s)
    client = get_webpy_client(a.tenant_id, s)
    source = "seed" if a.depth == 0 else "prev_employer"
    target = client.create_target(
        role=a.role_id, name=a.company, source=source, depth=a.depth
    )
    try:
        employees = bd.company_employees(a.company)
    except BrightdataNotFound:
        activity.logger.info("no employee data for %s", a.company)
        return ScanResult(new_candidates=0, leads=[])
    summary = ingest_recent_joiners(
        client, role_id=a.role_id, run_id=a.run_id, company=a.company,
        from_company_id=target.id, employees=employees,
        as_of=date.fromisoformat(a.as_of), window_days=a.window_days, depth=a.depth,
    )
    return ScanResult(new_candidates=summary.edges_created, leads=summary.leads)


@activity.defn(name="evaluate_candidate")
def evaluate_candidate(a: EvalArgs) -> EvalResult:
    s = get_settings()
    out = evaluate_lead(
        get_webpy_client(a.tenant_id, s), get_client(s), get_scorer(s),
        get_drafter(s),
        role_id=a.role_id, run_id=a.run_id, candidate_id=a.candidate_id,
        linkedin_url=a.linkedin_url, role_title=a.role_title, icp=a.icp,
        depth=a.depth, max_depth=a.max_depth,
        budget=Budget(max_leads=s.max_leads_per_run,
                      max_scrapes=s.max_scrapes_per_run),
        fetched_at=datetime.now(UTC).isoformat(),
    )
    return EvalResult(
        is_fit=out.is_fit, drafted=out.drafted, skipped=out.skipped,
        prev_employer_companies=out.prev_employer_companies,
        network_leads=out.network_leads,
    )


@activity.defn(name="finalize_run")
def finalize_run(a: FinalizeArgs) -> dict:
    s = get_settings()
    client = get_webpy_client(a.tenant_id, s)
    c = a.counters
    up = client.finalize_run(
        a.run_id,
        status=a.status,
        scanned_companies=c.get("scanned", 0),
        found_candidates=c.get("found", 0),
        fit_candidates=c.get("fit", 0),
        drafted=c.get("drafted", 0),
        budget_used=c.get("budget_used", 0),
    )
    return up.data


ALL_ACTIVITIES = [scan_company, evaluate_candidate, finalize_run]
