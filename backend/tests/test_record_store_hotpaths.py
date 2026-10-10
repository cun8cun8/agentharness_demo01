from copy import deepcopy
from threading import RLock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain.schemas import CreateTaskRequest, RunPhase
from app.infra.record_store import PostgresRecordStore
from app.infra.records import Base, StoreRecord, StoreConflictError
from app.infra.store import InMemoryStore


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("RESEARCHFORGE_PERSISTENCE", "0")
    monkeypatch.setenv("RESEARCHFORGE_STORE_BACKEND", "memory")
    get_settings.cache_clear()
    memory = InMemoryStore()
    task = memory.create_task(CreateTaskRequest(title="hot path", goal="repair"))
    run = memory.create_run(task.id, "repair_baseline_v1", "policy_default_v1", None)
    result = object.__new__(PostgresRecordStore)
    result.__dict__.update(memory.__dict__)
    result._engine = create_engine("sqlite://")
    result._mutex = RLock()
    result._unit_session = None
    result._baseline = {}
    Base.metadata.create_all(result._engine)
    records = result._memory_records({"runs", "tasks", "events", "audit_logs"})
    with Session(result._engine) as session, session.begin():
        for (collection, key), body in records.items():
            session.add(StoreRecord(collection=collection, entity_id=key, body=body))
    result._baseline.update(deepcopy(records))
    yield result, run
    result._engine.dispose()
    get_settings.cache_clear()


def test_runtime_update_does_not_serialize_unrelated_history(store):
    record_store, run = store
    class HistoricalRecord:
        run_id = "unrelated-run"
        def model_dump(self, **kwargs):
            raise AssertionError("historical record was serialized")
        def __deepcopy__(self, memo):
            raise AssertionError("historical record was copied")
    record_store.runs["history"] = HistoricalRecord()
    record_store.audit_logs["history"] = HistoricalRecord()
    record_store.steps["history"] = HistoricalRecord()
    record_store.update_run(run.id, metrics={"probe": True})
    step = record_store.add_step(run.id, RunPhase.PRECHECK, "verify", "hot path")
    with Session(record_store._engine) as session:
        assert session.get(StoreRecord, ("runs", run.id)).body["metrics"]["probe"] is True
        assert session.get(StoreRecord, ("steps", step.id)) is not None
        assert session.get(StoreRecord, ("runs", "history")) is None


def test_scoped_failure_rolls_back_memory_and_database(store, monkeypatch):
    record_store, run = store
    before_run = deepcopy(run)
    before_events = deepcopy(record_store.events[run.id])
    def fail(*args):
        raise StoreConflictError("concurrent write")
    monkeypatch.setattr(record_store, "_write_targeted_records", fail)
    with pytest.raises(StoreConflictError):
        record_store.update_run(run.id, metrics={"failed_write": True})
    assert record_store.runs[run.id] == before_run
    assert record_store.events[run.id] == before_events
    with Session(record_store._engine) as session:
        assert "failed_write" not in session.get(StoreRecord, ("runs", run.id)).body["metrics"]
