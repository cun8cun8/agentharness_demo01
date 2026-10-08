"""Optional Temporal adapter for durable repository repair workflows.

The local/default backend stays on the existing Redis queue. Temporal is loaded
only when ``RESEARCHFORGE_WORKFLOW_BACKEND=temporal`` is selected, so a local
developer does not need the SDK or a Temporal server.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.config import get_settings


def _imports():
    try:
        from temporalio import activity, workflow
        from temporalio.client import Client
        from temporalio.common import RetryPolicy
        from temporalio.worker import Worker
    except ImportError as exc:  # pragma: no cover - exercised in deployment
        raise RuntimeError(
            "TEMPORAL_SDK_NOT_INSTALLED: install backend/requirements-temporal.txt"
        ) from exc
    return activity, workflow, Client, RetryPolicy, Worker


def _workflow_type():
    activity, workflow, _client, RetryPolicy, _worker = _imports()

    @activity.defn(name="researchforge.execute_repository_repair")
    async def execute_repository_repair_activity(payload: dict[str, Any]) -> dict[str, Any]:
        from app.api.integrations import _execute_repository_repair_job

        return await _execute_repository_repair_job(
            str(payload["repository_id"]),
            str(payload["job_id"]),
            dict(payload["request"]),
        )

    @workflow.defn
    class RepositoryRepairWorkflow:
        @workflow.run
        async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
            return await workflow.execute_activity(
                execute_repository_repair_activity,
                payload,
                start_to_close_timeout=timedelta(
                    seconds=max(60, int(payload.get("activity_timeout_seconds", 2700)))
                ),
                retry_policy=RetryPolicy(
                    maximum_attempts=max(1, int(payload.get("maximum_attempts", 3))),
                    non_retryable_error_types=[
                        "ValueError",
                        "SandboxUnavailable",
                        "ModelInvocationError",
                    ],
                ),
            )

    return RepositoryRepairWorkflow, execute_repository_repair_activity


async def start_repository_repair_workflow(
    *,
    repository_id: str,
    job_id: str,
    request: dict[str, Any],
) -> str:
    _activity, _workflow, Client, _retry_policy, _worker = _imports()
    RepositoryRepairWorkflow, _execute_activity = _workflow_type()
    settings = get_settings()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    workflow_id = f"researchforge-repository-repair-{job_id}"
    await client.start_workflow(
        RepositoryRepairWorkflow.run,
        {
            "repository_id": repository_id,
            "job_id": job_id,
            "request": request,
            "activity_timeout_seconds": max(60, int(settings.agent_timeout_seconds) * 3),
            "maximum_attempts": max(1, int(settings.job_max_retries) + 1),
        },
        id=workflow_id,
        task_queue=settings.temporal_task_queue,
    )
    return workflow_id


async def run_temporal_worker(stop_event) -> None:
    _activity, _workflow, Client, _retry_policy, Worker = _imports()
    RepositoryRepairWorkflow, execute_activity = _workflow_type()
    settings = get_settings()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    async with Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[RepositoryRepairWorkflow],
        activities=[execute_activity],
    ):
        await stop_event.wait()
