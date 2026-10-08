from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from app.api.runs import _execute_run_job
from app.api.notebooks import execute_notebook_job
from app.api.research import execute_research_cycle
from app.api.benchmarks import _execute_golden_acceptance_job
from app.api.evaluations import (
    _execute_evaluation_job,
    _execute_strategy_comparison_job,
)
from app.api.integrations import _execute_repository_sync_job, _execute_repository_repair_job
from app.api.research import (
    _execute_notebook_run_job,
    _execute_research_benchmark_acceptance_job,
)
from app.api.datasets import _execute_training_bundle_export_job
from app.infra.store import store
from app.services.job_queue import JobQueueManager, job_queue
from app.services.event_bus import event_publisher
from app.services.observability import configure_observability
from app.services.training_controller import TrainingController
from app.services.sandbox_pool import sandbox_pool_controller
from app.config import get_settings


def register_worker_handlers(queue: JobQueueManager = job_queue) -> str:
    """Register handlers used by Redis payloads in a standalone worker."""
    run_handler_key = queue.register_handler(_execute_run_job)
    queue.register_handler(_execute_repository_sync_job)
    queue.register_handler(_execute_repository_repair_job)
    queue.register_handler(_execute_golden_acceptance_job)
    queue.register_handler(_execute_evaluation_job)
    queue.register_handler(_execute_strategy_comparison_job)
    queue.register_handler(_execute_notebook_run_job)
    queue.register_handler(execute_notebook_job)
    queue.register_handler(execute_research_cycle)
    queue.register_handler(_execute_research_benchmark_acceptance_job)
    queue.register_handler(_execute_training_bundle_export_job)
    return run_handler_key


async def run_worker(
    queue: JobQueueManager = job_queue,
    stop_event: asyncio.Event | None = None,
) -> None:
    if queue.backend != "redis" and get_settings().workflow_backend != "temporal":
        raise RuntimeError(
            "Standalone worker requires Redis or Temporal workflow configuration"
        )
    register_worker_handlers(queue)
    queue.set_before_handler(store.refresh)
    await queue.start(start_worker=True)
    shutdown = stop_event or asyncio.Event()
    controller = TrainingController(store)
    workflow_task = None
    try:
        if get_settings().workflow_backend == "temporal":
            from app.services.temporal_workflows import run_temporal_worker

            workflow_task = asyncio.create_task(
                run_temporal_worker(shutdown),
                name="researchforge-temporal-worker",
            )
        try:
            await asyncio.wait_for(event_publisher.start(), timeout=5)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Worker event bus unavailable: %s", type(exc).__name__,
            )
        await sandbox_pool_controller.start()
        await controller.start()
        await shutdown.wait()
    finally:
        if workflow_task is not None:
            workflow_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await workflow_task
        await sandbox_pool_controller.stop()
        await controller.stop()
        await queue.stop()
        await event_publisher.stop()


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    def request_shutdown(_signum: int, _frame: object) -> None:
        stop_event.set()

    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(signal_name, request_shutdown)
        except (AttributeError, OSError, ValueError):
            continue


def main() -> None:
    provider = configure_observability()
    async def serve() -> None:
        stop_event = asyncio.Event()
        _install_signal_handlers(stop_event)
        await run_worker(stop_event=stop_event)

    try:
        asyncio.run(serve())
    finally:
        if provider:
            provider.shutdown()


if __name__ == "__main__":
    main()
