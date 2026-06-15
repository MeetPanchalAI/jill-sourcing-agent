"""In-process sourcing runner — what the dashboard's "Start sourcing" button calls.

The durable *production* path is the Temporal workflow in ``apps/worker``. For the
self-contained portal we run the **same** pipeline (``jill.pipeline.run_sourcing``)
synchronously and write its output straight through the ORM via
``DjangoSourcingClient`` — no Temporal server, no HTTP round-trip. The mock crawl
finishes in well under a second, so a click returns ranked leads immediately.

LinkedIn data is always mocked here (a button must never trigger real scraping);
scoring and drafting use live Claude when ``JILL_MODE=live`` and a key is configured,
otherwise the deterministic mock judges. The ``jill`` imports are local to
``run_sourcing_inprocess`` so importing this module never pulls in the worker.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.utils import timezone

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
        obj, created = Candidate.objects.update_or_create(
            linkedin_url=url, defaults=f
        )
        return _Result(obj.id, created)

    def create_target(self, **f) -> _Result:
        role_id = f.pop("role")
        name = f.pop("name")
        if "discovered_from" in f:
            f["discovered_from_id"] = f.pop("discovered_from")
        obj, created = TargetCompany.objects.update_or_create(
            role_id=role_id, name=name, defaults=f
        )
        return _Result(obj.id, created)

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


def run_sourcing_inprocess(role: Role) -> SourcingRun:
    """Run the full monitor → enrich → score → expand → draft pipeline for ``role``
    and return the finished ``SourcingRun``. Synchronous and fast (mock crawl)."""
    from jill.agent.drafting import TemplateDrafter, get_drafter
    from jill.agent.scoring import RuleScorer, get_scorer
    from jill.brightdata.mock import MockBrightdataClient
    from jill.config import get_settings
    from jill.pipeline.run import run_sourcing

    settings = get_settings()
    try:
        scorer, drafter = get_scorer(settings), get_drafter(settings)
    except Exception:  # live judges unavailable (no key) → deterministic fallback
        scorer, drafter = RuleScorer(), TemplateDrafter()

    seeds = [
        c.get("name") if isinstance(c, dict) else c
        for c in role.icp.get("target_companies", [])
    ]
    seeds = [s for s in seeds if s]

    run = SourcingRun.objects.create(role=role, status=SourcingRun.Status.PENDING)
    run_sourcing(
        DjangoSourcingClient(),
        MockBrightdataClient(),  # never scrape real LinkedIn from a button
        scorer,
        drafter,
        role_id=role.id,
        run_id=run.id,
        role_title=role.title,
        icp=role.icp,
        seed_companies=seeds,
        as_of=date.today(),
    )
    run.refresh_from_db()
    return run
