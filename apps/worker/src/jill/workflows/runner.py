"""Temporal client/worker helpers used by the CLI and the long-running worker.

Determinism note: the worker runs sync activities in a thread pool; the workflow
itself never touches IO. Scheduling uses Temporal Schedules so the monitor reruns
on a cron without an external scheduler.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleSpec,
)
from temporalio.worker import Worker

from .activities import ALL_ACTIVITIES
from .types import SourcingInput
from .workflow import SourcingRunWorkflow

TASK_QUEUE = "jill-sourcing"


def temporal_address() -> str:
    return os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")


async def connect() -> Client:
    return await Client.connect(temporal_address())


async def run_worker(client: Client | None = None) -> None:
    client = client or await connect()
    with ThreadPoolExecutor(max_workers=8) as pool:
        worker = Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[SourcingRunWorkflow],
            activities=ALL_ACTIVITIES,
            activity_executor=pool,
        )
        await worker.run()


async def start_sourcing_run(client: Client, inp: SourcingInput) -> dict:
    """Start the workflow and await its result (used by ``jill source``)."""
    return await client.execute_workflow(
        SourcingRunWorkflow.run,
        inp,
        id=f"sourcing-run-{inp.tenant_id}-{inp.run_id}",
        task_queue=TASK_QUEUE,
    )


async def create_schedule(client: Client, inp: SourcingInput, cron: str,
                          schedule_id: str) -> str:
    await client.create_schedule(
        schedule_id,
        Schedule(
            action=ScheduleActionStartWorkflow(
                SourcingRunWorkflow.run,
                inp,
                id=f"sourcing-sched-{inp.tenant_id}-{inp.role_id}",
                task_queue=TASK_QUEUE,
            ),
            spec=ScheduleSpec(cron_expressions=[cron]),
        ),
    )
    return schedule_id
