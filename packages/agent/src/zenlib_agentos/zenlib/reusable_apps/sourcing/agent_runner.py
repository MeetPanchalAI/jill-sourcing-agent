"""In-process sourcing runner — what the dashboard's "Start sourcing" button calls.

The durable *production* path is the Temporal workflow in ``apps/worker``. For the
self-contained portal we run the **same** pipeline (``jill.pipeline.run_sourcing``)
synchronously and write its output straight through the ORM via
``DjangoSourcingClient`` — no Temporal server, no HTTP round-trip.

Data sources follow ``JILL_MODE``:

* **mock**  — LinkedIn from fixtures, deterministic rule scorer/template drafter.
              Finishes in well under a second; zero network, zero cost.
* **live**  — real LinkedIn scraping via the Brightdata Web Scraper API **and** live
              Claude scoring/drafting. Real scrapes are slow + cost money, so live
              runs are tightly bounded (``Settings.live_max_*``). Failures are
              surfaced as a FAILED run (with reason) rather than silently faked.

The ``jill`` imports are local to ``run_sourcing_inprocess`` so importing this
module never pulls in the worker.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger("jill.sourcing")


def _configure_logging() -> None:
    """Attach a clean, readable handler to the ``jill`` loggers once, so a live
    run is observable in the dev-server console without Django LOGGING config."""
    root = logging.getLogger("jill")
    if getattr(root, "_jill_handler_attached", False):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s  jill  %(levelname)-5s  %(message)s", datefmt="%H:%M:%S",
    ))
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    root.propagate = False
    root._jill_handler_attached = True  # type: ignore[attr-defined]

from .models import (
    Candidate,
    Enrichment,
    LeadEdge,
    OutreachDraft,
    Role,
    Score,
    SourcingRun,
    TargetCompany,
)

_TERMINAL = {
    SourcingRun.Status.COMPLETED,
    SourcingRun.Status.FAILED,
    SourcingRun.Status.BUDGET_EXHAUSTED,
}


@dataclass
class _Result:
    """Mirrors jill's ``Upserted`` (``.id`` + ``.created``) so the pipeline, which
    is written against the HTTP client, works unchanged against the ORM."""

    id: int
    created: bool


class DjangoSourcingClient:
    """Writes pipeline output directly through the ORM, with the same idempotent
    upsert semantics as the HTTP API (so re-runs converge instead of duplicating).

    Foreign keys arrive as plain ids under the model field name (``role``,
    ``to_candidate``, …); we pass them to the ORM with the ``_id`` suffix. Every
    write runs inside the request's tenant context, so RLS scopes it automatically.
    """

    def upsert_candidate(self, **f) -> _Result:
        url = f.pop("linkedin_url")
        if "first_seen_run" in f:
            f["first_seen_run_id"] = f.pop("first_seen_run")
        # These columns are NOT NULL (blank=""); a sparse source (e.g. a
        # people_also_viewed peer with a null "about") can hand us None. Coerce to
        # "" so it doesn't violate the not-null constraint. (started_current_role_at
        # is nullable, so its None is fine and left alone.)
        for key in ("full_name", "headline", "current_company", "current_title", "location"):
            if f.get(key) is None and key in f:
                f[key] = ""
        # Match on the unique key INCLUDING soft-deleted rows. The default manager
        # hides soft-deleted candidates, so a plain update_or_create can't see one
        # and would collide on the (tenant, linkedin_url) unique index. Revive +
        # update instead, so re-discovery is always idempotent (no inconsistency).
        obj = Candidate.objects.all_with_deleted().filter(linkedin_url=url).first()
        # Isolate the write in a savepoint: if a single record still trips a DB
        # constraint, only this savepoint rolls back — the run's transaction stays
        # usable (so the rest of the crawl, and the FAILED-status write, succeed).
        with transaction.atomic():
            if obj is None:
                obj = Candidate.objects.create(linkedin_url=url, **f)
                return _Result(obj.id, True)
            for key, val in f.items():
                setattr(obj, key, val)
            obj.is_deleted = False
            obj.deleted_at = None
            obj.save()
            return _Result(obj.id, False)

    def create_target(self, **f) -> _Result:
        role_id = f.pop("role")
        name = f.pop("name")
        if "discovered_from" in f:
            f["discovered_from_id"] = f.pop("discovered_from")
        obj, created = TargetCompany.objects.update_or_create(
            role_id=role_id, name=name, defaults=f
        )
        return _Result(obj.id, created)

    # --- cross-run state (idempotent re-runs: never re-scrape what's done) ---

    def evaluated_candidate_ids(self, role_id: int) -> set[int]:
        """Candidates already scored for this role (from any prior workflow) — so a
        re-run doesn't re-enrich/re-score people already on the list."""
        return set(
            Score.objects.filter(role_id=role_id).values_list("candidate_id", flat=True)
        )

    def scanned_companies(self, role_id: int) -> list[str]:
        """Company seeds already scanned in a prior workflow — so promoting their
        companies again doesn't re-scrape the same company page."""
        return list(
            TargetCompany.objects.filter(
                role_id=role_id, last_scanned_at__isnull=False
            ).values_list("name", flat=True)
        )

    def mark_company_scanned(self, target_id: int) -> None:
        TargetCompany.objects.filter(id=target_id).update(last_scanned_at=timezone.now())

    def create_lead_edge(self, **f) -> _Result:
        lookup = {
            "role_id": f.get("role"),
            "to_candidate_id": f.get("to_candidate"),
            "kind": f.get("kind"),
            "from_company_id": f.get("from_company"),
            "from_candidate_id": f.get("from_candidate"),
            "depth": f.get("depth", 0),
        }
        extra = {"run_id": f.get("run"), "method": f.get("method", "")}
        obj, created = LeadEdge.objects.get_or_create(defaults=extra, **lookup)
        return _Result(obj.id, created)

    def upsert_enrichment(self, **f) -> _Result:
        cand_id = f.pop("candidate")
        obj, created = Enrichment.objects.update_or_create(
            candidate_id=cand_id, defaults=f
        )
        return _Result(obj.id, created)

    def upsert_score(self, **f) -> _Result:
        cand_id = f.pop("candidate")
        role_id = f.pop("role")
        obj, created = Score.objects.update_or_create(
            candidate_id=cand_id, role_id=role_id, defaults=f
        )
        return _Result(obj.id, created)

    def create_outreach(self, **f) -> _Result:
        lookup = {
            "candidate_id": f.pop("candidate"),
            "role_id": f.pop("role"),
            "channel": f.pop("channel"),
        }
        obj, created = OutreachDraft.objects.get_or_create(defaults=f, **lookup)
        return _Result(obj.id, created)

    def finalize_run(self, run_id: int, **f) -> _Result:
        run = SourcingRun.objects.get(id=run_id)
        for key, val in f.items():
            setattr(run, key, val)
        if run.status == SourcingRun.Status.RUNNING and run.started_at is None:
            run.started_at = timezone.now()
        if run.status in _TERMINAL and run.finished_at is None:
            run.finished_at = timezone.now()
        run.save()
        return _Result(run.id, False)


def _build_clients(settings):
    """Pick LinkedIn + judge implementations for the active mode.

    Returns ``(brightdata, scorer, drafter, source_label)``. In live mode the real
    Brightdata client and live Claude judges are used; if a live judge can't be
    constructed (e.g. missing key) we fall back to the deterministic judge for that
    piece only, and log it — but LinkedIn data is never silently faked in live mode.
    """
    from jill.agent.drafting import TemplateDrafter, get_drafter
    from jill.agent.scoring import RuleScorer, get_scorer
    from jill.brightdata.mock import MockBrightdataClient

    try:
        scorer, drafter = get_scorer(settings), get_drafter(settings)
    except Exception as exc:  # live judges unavailable → deterministic fallback
        logger.warning("live judges unavailable (%s) → using rule scorer/template "
                       "drafter", exc)
        scorer, drafter = RuleScorer(), TemplateDrafter()

    if settings.is_live:
        from jill.brightdata.live import LiveBrightdataClient
        return LiveBrightdataClient(settings), scorer, drafter, "Brightdata (LIVE)"
    return MockBrightdataClient(), scorer, drafter, "fixtures (MOCK)"


def _autoplan_seeds(role: Role, planner) -> list[str]:
    """Ask the planner for the top seed companies, persist them to the role's
    ``strategy`` (+ ``icp.target_companies``), and return seed values for the run.

    Returns ``[]`` if planning is unavailable, so the run still completes cleanly
    (it simply finds nothing rather than crashing)."""
    if planner is None:
        return []
    try:
        plan = planner.propose_seeds(role.title, role.icp or {}, n=3)
    except Exception as exc:
        logger.warning("PLAN     seed planning unavailable (%s) — no seeds to crawl", exc)
        return []

    companies = [{"name": c.name, "linkedin_url": c.linkedin_url} for c in plan.companies]
    logger.info("PLAN     no seed given — proposed %d compan%s  [%s]",
                len(companies), "y" if len(companies) == 1 else "ies", planner.model_id)
    for c in plan.companies:
        logger.info("           - %-24s %s", c.name, c.linkedin_url or "(no URL)")
        if c.reason:
            logger.info("             %s", c.reason)

    icp = role.icp or {}
    icp["target_companies"] = companies
    strategy = role.strategy or {}
    strategy["target_companies"] = companies
    role.icp, role.strategy = icp, strategy
    role.save(update_fields=["icp", "strategy"])
    return [c["linkedin_url"] or c["name"] for c in companies]


def _promoted_seeds(role: Role) -> list[str]:
    """WF(N+1) seeds = the unique set of companies the candidates surviving WF(N)
    belong to. Keyed by each candidate's company URL (captured at enrichment) so
    we seed the right org, not a name guess; falls back to the company name when
    no URL was stored. This is what "build the next workflow on the previous
    workflow's curated results" means — and it equals the Monitoring projection.
    """
    cands = (
        Candidate.objects.filter(scores__role=role)
        .select_related("enrichment")
        .distinct()
    )
    seen: set[str] = set()
    seeds: list[str] = []
    for c in cands:
        raw = getattr(getattr(c, "enrichment", None), "raw", None) or {}
        url = (raw.get("current_company_url") or "").strip()
        seed = url or (c.current_company or "").strip()
        key = (url.rstrip("/") or (c.current_company or "")).lower()
        if not seed or key in seen:
            continue
        seen.add(key)
        seeds.append(seed)
    return seeds


def run_sourcing_inprocess(role: Role) -> SourcingRun:
    """Run the full monitor → enrich → score → expand → draft pipeline for ``role``
    and return the finished ``SourcingRun``.

    Mock mode is sub-second; live mode does real Brightdata scraping (bounded by
    ``Settings.live_max_*``) and live Claude judging, and can take a while."""
    from jill.config import get_settings
    from jill.pipeline.run import run_sourcing

    _configure_logging()
    settings = get_settings()

    # Prefer the LinkedIn company URL over the bare name — a name can resolve to
    # the wrong org, the URL is unambiguous (see jill.brightdata.live).
    seeds = [
        (c.get("linkedin_url") or c.get("name")) if isinstance(c, dict) else c
        for c in role.icp.get("target_companies", [])
    ]
    seeds = [s for s in seeds if s]

    # Cross-workflow growth: once the role has a curated list, the *next* run seeds
    # from the unique set of companies those survivors belong to (= the Monitoring
    # projection). The manual seed only bootstraps the very first run.
    promoted = _promoted_seeds(role)
    if promoted:
        logger.info("PROMOTE  seeding from %d compan%s on the curated list "
                    "(prior workflow's survivors)",
                    len(promoted), "y" if len(promoted) == 1 else "ies")
        seeds = promoted

    # Auto seed-company discovery (planner + mid-run replan) is OFF by default —
    # seeds are manual + cross-run-promoted only. Enable with JILL_AUTOPLAN=1.
    planner = None
    if settings.autoplan:
        try:
            from jill.agent.planning import get_planner
            planner = get_planner(settings)
        except Exception as exc:
            logger.warning("planner unavailable (%s) — auto-seed/replan disabled", exc)
            planner = None
        if not seeds:
            seeds = _autoplan_seeds(role, planner)

    brightdata, scorer, drafter, source = _build_clients(settings)
    bounds = (
        dict(max_depth=settings.live_max_depth,
             max_leads=settings.live_max_leads,
             max_companies=settings.live_max_companies)
        if settings.is_live else
        dict(max_depth=settings.max_expansion_depth,
             max_leads=settings.max_leads_per_run,
             max_companies=settings.max_scrapes_per_run)
    )

    run = SourcingRun.objects.create(role=role, status=SourcingRun.Status.PENDING)
    logger.info("=" * 68)
    logger.info("SOURCING START  role=%s (#%s)  run=#%s", role.title, role.id, run.id)
    logger.info("  mode=%s  LinkedIn=%s", settings.mode, source)
    logger.info("  scorer=%s  drafter=%s",
                type(scorer).__name__, type(drafter).__name__)
    logger.info("  seeds=%s  bounds=%s", seeds, bounds)
    logger.info("=" * 68)

    try:
        result = run_sourcing(
            DjangoSourcingClient(), brightdata, scorer, drafter,
            role_id=role.id, run_id=run.id, role_title=role.title,
            icp=role.icp, seed_companies=seeds, as_of=date.today(),
            planner=planner, expand_min_score=settings.expand_min_score,
            expand_network=settings.expand_network,
            dedup_cross_run=settings.cross_run_dedup, **bounds,
        )
        logger.info("SOURCING DONE   run=#%s status=%s | scanned=%d found=%d "
                    "fit=%d drafted=%d", run.id, result.status, result.scanned,
                    result.found, result.fit, result.drafted)
    except Exception as exc:
        # Don't fake success: mark the run FAILED so the UI run row shows it, and
        # log the full reason. Swallow the exception so the page still renders.
        logger.exception("SOURCING FAILED run=#%s: %s: %s",
                         run.id, type(exc).__name__, exc)
        run.refresh_from_db()
        run.status = SourcingRun.Status.FAILED
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at"])

    run.refresh_from_db()
    return run
