"""P7 gate (tests.md §8): the SourcingRunWorkflow orchestration — BFS counts,
budget, dedup, and replayer-clean determinism. Runs in Temporal's time-skipping
test env with mock activities (the workflow logic is what's under test)."""

from __future__ import annotations

from jill.workflows.types import (
    EvalArgs,
    EvalResult,
    FinalizeArgs,
    ScanArgs,
    ScanResult,
    SourcingInput,
)
from jill.workflows.workflow import SourcingRunWorkflow
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

TQ = "test-sourcing"


@activity.defn(name="scan_company")
async def scan(a: ScanArgs) -> ScanResult:
    data = {
        "vapi": [{"id": 1, "linkedin_url": "u1"}, {"id": 2, "linkedin_url": "u2"}],
        "retell ai": [{"id": 3, "linkedin_url": "u3"}],
    }
    leads = data.get(a.company.strip().lower(), [])
    return ScanResult(new_candidates=len(leads), leads=leads)


@activity.defn(name="evaluate_candidate")
async def evaluate(a: EvalArgs) -> EvalResult:
    # Candidate 1 (a fit) expands: prev employer Retell + one network lead.
    if a.candidate_id == 1:
        nets = [{"id": 9, "linkedin_url": "u9"}] if a.depth < a.max_depth else []
        return EvalResult(True, 2, False, ["Retell AI"], nets)
    return EvalResult(False, 0, False, [], [])


@activity.defn(name="finalize_run")
async def finalize(a: FinalizeArgs) -> dict:
    return {"run_id": a.run_id, "status": a.status, **a.counters}


ACTS = [scan, evaluate, finalize]


def _input(**over) -> SourcingInput:
    base = dict(
        role_id=1, run_id=42, tenant_id=1, role_title="Voice AI Engineer",
        icp={"must_have_skills": ["Python"]}, seed_companies=["Vapi"],
        max_depth=2, window_days=90, max_leads=50, max_companies=50,
    )
    base.update(over)
    return SourcingInput(**base)


async def _execute(inp: SourcingInput):
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue=TQ,
                          workflows=[SourcingRunWorkflow], activities=ACTS):
            handle = await env.client.start_workflow(
                SourcingRunWorkflow.run, inp, id=f"wf-{inp.run_id}", task_queue=TQ,
            )
            result = await handle.result()
            history = await handle.fetch_history()
    return result, history


async def test_happy_path_bfs_counts():
    result, _ = await _execute(_input())
    assert result["status"] == "completed"
    assert result["scanned"] == 2       # Vapi (seed) + Retell (prev employer)
    assert result["found"] == 3         # 2 Vapi joiners + 1 Retell joiner
    assert result["evaluated"] == 4     # candidates 1, 2, 9, 3
    assert result["fit"] == 1
    assert result["drafted"] == 2


async def test_budget_caps_evaluation():
    result, _ = await _execute(_input(max_leads=1))
    assert result["status"] == "budget_exhausted"
    assert result["evaluated"] == 1


async def test_seed_company_dedup():
    result, _ = await _execute(_input(seed_companies=["Vapi", "vapi", "VAPI"]))
    assert result["scanned"] == 2  # Vapi scanned once, plus Retell


async def test_replayer_is_deterministic():
    _, history = await _execute(_input())
    await Replayer(workflows=[SourcingRunWorkflow]).replay_workflow(history)
