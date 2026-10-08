import asyncio
from datetime import datetime, timezone

from app.agent.autonomous import run_lease
from app.config import get_settings
from app.services import training

_lock = asyncio.Lock()


def enqueue_training(store, record):
    if not get_settings().training_controller_enabled:
        raise ValueError("TRAINING_CONTROLLER_DISABLED")
    if record["status"] != "prepared":
        raise ValueError("TRAINING_NOT_PREPARED")
    record.update(status="queued", queued_at=training.now())
    store._persist()
    return record


async def training_action(store, job_id, action):
    async with _lock, run_lease("training-controller"):
        if get_settings().store_backend == "postgres":
            store.refresh()
        handlers = {"start": enqueue_training, "enqueue": enqueue_training,
                    "refresh": training.refresh_training, "cancel": training.cancel_training}
        return await asyncio.to_thread(handlers[action], store, store.training_jobs[job_id])


class TrainingController:
    def __init__(self, store):
        self.store = store
        self.task = None
        self.stop_event = None
        self.last_tick = None
        self.last_error = None

    async def tick(self):
        async with _lock, run_lease("training-controller"):
            if get_settings().store_backend == "postgres":
                self.store.refresh()
            active = [item for item in self.store.training_jobs.values() if item["status"] in {"starting", "running"}]
            for item in active:
                try:
                    handler = training.start_training if item["status"] == "starting" else training.refresh_training
                    await asyncio.to_thread(handler, self.store, item)
                    item.pop("controller_error", None)
                except Exception as exc:
                    item["controller_error"] = type(exc).__name__
                    self.last_error = "TRAINING_BACKEND_UNAVAILABLE"
            capacity = get_settings().training_concurrency
            running = sum(item["status"] in {"starting", "running"} for item in self.store.training_jobs.values())
            queued = sorted((item for item in self.store.training_jobs.values() if item["status"] == "queued"),
                            key=lambda item: (item["queued_at"], item["id"]))
            for item in queued[:max(0, capacity - running)]:
                item["status"] = "prepared"
                try:
                    await asyncio.to_thread(training.start_training, self.store, item)
                except ValueError as exc:
                    item.update(status="failed", error=str(exc), finished_at=training.now())
                except Exception as exc:
                    item["controller_error"] = type(exc).__name__
                    if item["status"] == "prepared":
                        item["status"] = "queued"
                    self.last_error = "TRAINING_BACKEND_UNAVAILABLE"
            self.store._persist()
            self.last_tick = datetime.now(timezone.utc).isoformat()

    async def _run(self):
        while not self.stop_event.is_set():
            try:
                self.last_error = None
                await self.tick()
            except RuntimeError as exc:
                if str(exc) != "RUN_ALREADY_EXECUTING":
                    self.last_error = type(exc).__name__
            except Exception as exc:
                self.last_error = type(exc).__name__
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=get_settings().training_poll_seconds)
            except asyncio.TimeoutError:
                pass

    async def start(self):
        if get_settings().training_controller_enabled and self.task is None:
            self.stop_event = asyncio.Event()
            self.task = asyncio.create_task(self._run(), name="training-controller")

    async def stop(self):
        if self.task:
            self.stop_event.set()
            await self.task
            self.task = None

    def status(self):
        return {"enabled": get_settings().training_controller_enabled,
                "running": self.task is not None and not self.task.done(), "last_tick": self.last_tick,
                "last_error": self.last_error, "concurrency": get_settings().training_concurrency,
                "backend": training.training_backend()}
