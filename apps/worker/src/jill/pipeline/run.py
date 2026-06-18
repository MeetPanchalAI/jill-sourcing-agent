"""In-process sourcing runner — the same frontier-BFS as the Temporal workflow,
executed inline.

This is the dev/demo escape hatch: ``jill source --local`` and the e2e smoke test
use it to run the full monitor → enrich → score → expand → draft pipeline without
a Temporal server. The durable production path is the workflow; this mirrors its
logic against injected clients.
"""

from __future__ import annotations

import logging
import re
from collections import deque
from dataclasses import dataclass
from datetime import UTC, date, datetime

from ..agent.scoring import Scorer
from ..brightdata.base import BrightdataClient
from ..brightdata.errors import BrightdataError, BrightdataNotFound
from .evaluate import evaluate_lead
from .expand import Budget
from .ingest import ingest_company_members, ingest_recent_joiners

logger = logging.getLogger("jill.pipeline")


def _is_profile_url(seed: str) -> bool:
    """A LinkedIn *profile* URL is a candidate to source directly; a company
    name/URL is something to monitor for joiners."""
    return "/in/" in seed


def _company_key(seed: str) -> str:
    """Canonical key for a company seed (URL or name) so the same org dedupes
    across runs whether it arrives as a linkedin.com/company/<slug> URL or a name."""
    s = (seed or "").strip().lower()
    if "linkedin.com/company/" in s:
        return s.split("linkedin.com/company/")[1].strip("/").split("?")[0]
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


@dataclass
class RunResult:
    status: str
    scanned: int = 0
    found: int = 0
    evaluated: int = 0
    surfaced: int = 0  # passed triage → on the shortlist
    fit: int = 0
    drafted: int = 0


_MAX_REPLANS = 2  # how many times to ask the planner for fresh seeds when we find 0 fit


def _replan_seeds(planner, role_title: str, icp: dict, *, tried: set[str], n: int = 3) -> list[str]:
    """Ask the planner for fresh seed companies, excluding ones already tried.

    Returns seed strings (LinkedIn company URLs, ideally) not seen before."""
    try:
        plan = planner.propose_seeds(role_title, icp, n=n, exclude=sorted(tried))
    except Exception as exc:
        logger.warning("REPLAN   planner unavailable (%s)", exc)
        return []
    fresh: list[str] = []
    for c in plan.companies:
        seed = (c.linkedin_url or c.name or "").strip()
        if seed and seed.lower() not in tried:
            fresh.append(seed)
            logger.info("           + %-24s %s", c.name, c.linkedin_url or "(no URL)")
    return fresh


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
    planner=None,
    expand_min_score: int = 0,
) -> RunResult:
    as_of = as_of or datetime.now(UTC).date()
    # Profile-URL seeds are sourced directly (real fetch → score → draft); company
    # seeds are monitored for recent joiners (needs Brightdata company discovery).
    company_seeds = [c for c in seed_companies if not _is_profile_url(c)]
    profile_seeds = [c for c in seed_companies if _is_profile_url(c)]
    companies: deque = deque((c, 0) for c in company_seeds)
    candidates: deque = deque()
    seen_companies: set[str] = set()
    seen_candidates: set[int] = set()
    # Cross-run idempotency: never re-scan a company or re-score a candidate that an
    # earlier workflow already handled (the new run builds on the prior, not over it).
    scanned_keys: set[str] = {
        _company_key(c) for c in getattr(client, "scanned_companies", lambda _r: [])(role_id)
    }
    evaluated: set[int] = set(
        getattr(client, "evaluated_candidate_ids", lambda _r: set())(role_id)
    )
    r = RunResult(status="running")
    budget_hit = False

    if company_seeds:
        logger.info("SEED     %d company seed(s): %s",
                    len(company_seeds), ", ".join(company_seeds))
    for url in profile_seeds:
        cand = client.upsert_candidate(linkedin_url=url, first_seen_run=run_id)
        candidates.append((cand.id, url, 0))
        r.found += 1
        logger.info("SEED     profile %s → candidate #%s queued", url, cand.id)
    if profile_seeds:
        _report(client, run_id, "running", r)

    replans = 0

    def _maybe_replan() -> bool:
        """When the frontier empties with zero fit candidates, pull a fresh seed
        company from the planner instead of ending empty-handed. Bounded by
        ``_MAX_REPLANS`` and the company budget."""
        nonlocal replans
        if not (planner and r.fit == 0 and not budget_hit
                and r.scanned < max_companies and replans < _MAX_REPLANS):
            return False
        replans += 1
        logger.info("=" * 64)
        logger.info("REPLAN   0 fit so far — asking the planner for fresh seed "
                    "companies (attempt %d/%d)", replans, _MAX_REPLANS)
        fresh = _replan_seeds(planner, role_title, icp, tried=seen_companies)
        for c in fresh:
            companies.append((c, 0))
        if not fresh:
            logger.info("REPLAN   no fresh companies proposed — stopping")
        return bool(fresh)

    while companies or candidates or _maybe_replan():
        if companies:
            company, depth = companies.popleft()
            key = _company_key(company)
            if key in seen_companies:
                continue
            seen_companies.add(key)
            if key in scanned_keys:
                logger.info("-" * 64)
                logger.info("MONITOR  %s — already scanned in an earlier workflow, "
                            "skipping", company)
                continue
            if r.scanned >= max_companies:
                budget_hit = True
                continue
            target = client.create_target(
                role=role_id, name=company,
                source="seed" if depth == 0 else "prev_employer", depth=depth,
            )
            logger.info("-" * 64)
            logger.info("MONITOR  %s   (depth %d, %s)", company, depth,
                        "seed" if depth == 0 else "prev-employer")
            logger.info("         fetching members from the LinkedIn company page ...")
            try:
                employees = brightdata.company_employees(company)
            except BrightdataNotFound:
                employees = []
            except BrightdataError as exc:
                # Skip this company (don't fail the whole run); the reason is logged.
                logger.warning("         discovery unavailable: %s — skipping", exc)
                r.scanned += 1
                continue

            if not employees:
                logger.info("         no members found - skipping")
                r.scanned += 1
                getattr(client, "mark_company_scanned", lambda _t: None)(target.id)
                scanned_keys.add(key)
                continue
            summary = ingest_recent_joiners(
                client, role_id=role_id, run_id=run_id, company=company,
                from_company_id=target.id, employees=employees, as_of=as_of,
                window_days=window_days, depth=depth,
            )
            r.scanned += 1
            det = summary.detection
            dateless = det.total > 0 and det.excluded_no_date == det.total
            if summary.leads or not dateless:
                logger.info("         %d members surfaced, %d recent joiner(s) "
                            "-> queued for scoring", len(employees),
                            len(summary.leads))
            else:
                # No employee carried a join date (company-page listings don't),
                # so recent-joiner detection excluded them all. Source every
                # surfaced member for role-fit scoring instead of yielding zero.
                # (Mock fixtures carry dates, so this only fires on live runs.)
                summary = ingest_company_members(
                    client, role_id=role_id, run_id=run_id, company=company,
                    from_company_id=target.id, employees=employees, depth=depth,
                )
                logger.info("         %d members surfaced, no join dates -> "
                            "all queued for role-fit scoring",
                            len(employees))
            r.found += summary.edges_created
            getattr(client, "mark_company_scanned", lambda _t: None)(target.id)
            scanned_keys.add(key)
            for lead in summary.leads:
                candidates.append((lead["id"], lead["linkedin_url"], depth))
            _report(client, run_id, "running", r)  # live progress for the dashboard
            continue

        cid, url, depth = candidates.popleft()
        if cid in seen_candidates or cid in evaluated:
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
            expand_min_score=expand_min_score,
        )
        r.evaluated += 1
        evaluated.add(cid)
        if out.is_fit:
            r.fit += 1
            r.drafted += out.drafted
        if out.surfaced:
            r.surfaced += 1
            # Grow the frontier from anyone who passed triage (recall), not only fits.
            for comp in out.prev_employer_companies:
                companies.append((comp, depth + 1))
            for lead in out.network_leads:
                candidates.append((lead["id"], lead["linkedin_url"], depth + 1))

    r.status = "budget_exhausted" if budget_hit else "completed"
    _report(client, run_id, r.status, r)

    logger.info("=" * 64)
    logger.info("FUNNEL   scanned %d companies  ->  evaluated %d  ->  shortlist %d "
                "(triage-kept)", r.scanned, r.evaluated, r.surfaced)
    logger.info("         fit %d  ->  drafted %d     status: %s",
                r.fit, r.drafted, r.status.upper())
    logger.info("=" * 64)
    return r
