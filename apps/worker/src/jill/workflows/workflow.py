"""SourcingRunWorkflow — the durable frontier-BFS over the lead graph.

The workflow owns the frontier (a company queue + a candidate queue), the
visited sets, the counters, and the depth/budget bounds. It is deterministic:
the only clock it reads is ``workflow.now()``, the queues/sets/counters are plain
in-memory state, and every side effect (scrape, score, draft, DB write) is an
activity. Re-running it replays identically.
"""

from __future__ import annotations

from collections import deque
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from .types import (
        EvalArgs,
        EvalResult,
        FinalizeArgs,
        ScanArgs,
        ScanResult,
        SourcingInput,
    )

_OPTS = {
    "start_to_close_timeout": timedelta(seconds=60),
    "retry_policy": RetryPolicy(maximum_attempts=3),
}


@workflow.defn
class SourcingRunWorkflow:
    @workflow.run
    async def run(self, inp: SourcingInput) -> dict:
        as_of = workflow.now().date().isoformat()
        await workflow.execute_activity(
            "finalize_run",
            FinalizeArgs(inp.run_id, inp.tenant_id, "running", {}),
            result_type=dict, **_OPTS,
        )

        companies: deque = deque((c, 0) for c in inp.seed_companies)
        candidates: deque = deque()
        seen_companies: set[str] = set()
        seen_candidates: set[int] = set()
        counters = {"scanned": 0, "found": 0, "fit": 0, "drafted": 0, "evaluated": 0}
        budget_hit = False

        while companies or candidates:
            # Companies first (breadth-first by discovery order).
            if companies:
                company, depth = companies.popleft()
                key = company.strip().lower()
                if key in seen_companies:
                    continue
                seen_companies.add(key)
                if counters["scanned"] >= inp.max_companies:
                    budget_hit = True
                    continue
                res: ScanResult = await workflow.execute_activity(
                    "scan_company",
                    ScanArgs(inp.role_id, inp.run_id, inp.tenant_id, company,
                             depth, as_of, inp.window_days),
                    result_type=ScanResult, **_OPTS,
                )
                counters["scanned"] += 1
                counters["found"] += res.new_candidates
                for lead in res.leads:
                    candidates.append((lead["id"], lead["linkedin_url"], depth))
                # Push live progress so the dashboard's run counters tick up.
                await workflow.execute_activity(
                    "finalize_run",
                    FinalizeArgs(inp.run_id, inp.tenant_id, "running", dict(counters)),
                    result_type=dict, **_OPTS,
                )
                continue

            # Then candidates.
            cid, url, depth = candidates.popleft()
            if cid in seen_candidates:
                continue
            seen_candidates.add(cid)
            if counters["evaluated"] >= inp.max_leads:
                budget_hit = True
                continue
            ev: EvalResult = await workflow.execute_activity(
                "evaluate_candidate",
                EvalArgs(inp.role_id, inp.run_id, inp.tenant_id, cid, url,
                         inp.role_title, inp.icp, depth, inp.max_depth),
                result_type=EvalResult, **_OPTS,
            )
            counters["evaluated"] += 1
            if ev.is_fit:
                counters["fit"] += 1
                counters["drafted"] += ev.drafted
                for comp in ev.prev_employer_companies:
                    companies.append((comp, depth + 1))
                for lead in ev.network_leads:
                    candidates.append((lead["id"], lead["linkedin_url"], depth + 1))

        status = "budget_exhausted" if budget_hit else "completed"
        counters["budget_used"] = counters["evaluated"]
        await workflow.execute_activity(
            "finalize_run",
            FinalizeArgs(inp.run_id, inp.tenant_id, status, counters),
            result_type=dict, **_OPTS,
        )
        return {"status": status, **counters}
