import asyncio

from app.config import Settings
from app.services.job_queue import JobQueueManager
from app.worker import register_worker_handlers, run_worker


def test_standalone_worker_registers_agent_run_handler() -> None:
    queue = JobQueueManager(
        settings=Settings(
            store_path="D:/code/ResearchForge Agent Harness/.data/worker-test.json",
            job_queue_backend="redis",
        ),
        redis_client_factory=lambda _url: None,
    )
    handler_key = register_worker_handlers(queue)
    assert handler_key == "app.api.runs:_execute_run_job"
    assert handler_key in queue._handlers
    assert "app.api.integrations:_execute_repository_sync_job" in queue._handlers
    assert "app.api.integrations:_execute_repository_repair_job" in queue._handlers
    assert "app.api.benchmarks:_execute_golden_acceptance_job" in queue._handlers
    assert "app.api.evaluations:_execute_strategy_comparison_job" in queue._handlers
    assert "app.api.research:_execute_notebook_run_job" in queue._handlers
    assert "app.api.research:_execute_research_benchmark_acceptance_job" in queue._handlers


def test_standalone_worker_rejects_local_backend() -> None:
    queue = JobQueueManager(
        settings=Settings(
            store_path="D:/code/ResearchForge Agent Harness/.data/worker-test.json",
            job_queue_backend="local_background",
        )
    )

    async def run() -> None:
        try:
            await run_worker(queue, asyncio.Event())
        except RuntimeError as exc:
            assert "requires" in str(exc)
        else:  # pragma: no cover - defensive assertion
            raise AssertionError("local worker backend should be rejected")

    asyncio.run(run())

def test_worker_keeps_processing_when_event_bus_is_unavailable(monkeypatch):
    from app import worker
    stop=asyncio.Event()
    stop.set()
    calls=[]
    class Queue:
        backend='redis'
        def set_before_handler(self,callback): pass
        async def start(self,**kwargs): calls.append('queue-start')
        async def stop(self): calls.append('queue-stop')
    class Controller:
        def __init__(self,*args): pass
        async def start(self): calls.append('controller-start')
        async def stop(self): pass
    class Events:
        async def start(self): raise ConnectionError('unavailable')
        async def stop(self): pass
    monkeypatch.setattr(worker,'register_worker_handlers',lambda queue:None)
    monkeypatch.setattr(worker,'event_publisher',Events())
    monkeypatch.setattr(worker,'TrainingController',Controller)
    monkeypatch.setattr(worker,'sandbox_pool_controller',Controller())
    asyncio.run(worker.run_worker(Queue(),stop))
    assert calls==['queue-start','controller-start','controller-start','queue-stop']
