from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock

from sqlalchemy import create_engine, select, text, func, or_, Table, MetaData, Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, aliased

from app.config import get_settings
from app.infra.migrations import MigrationRunner
from app.infra.records import (
    RepositoryConnectionRecord, StoreConflictError, StoreRecord, flatten_snapshot,
    inflate_records, merge_record,
)
from app.domain.schemas import (
    AgentRunResponse,
    AgentStep,
    Artifact,
    AuditLogResponse,
    CreateTraceDatasetItemRequest,
    CreateUserRequest,
    CreateRepositoryConnectionRequest,
    EvaluationRunResponse,
    JobResponse,
    PreferencePairResponse,
    RepositoryConnectionResponse,
    TaskResponse,
    TraceDatasetItemResponse,
    ToolCall,
    UpdateTraceDatasetItemRequest,
    UpdateUserRequest,
    UpdateRepositoryConnectionRequest,
    UserResponse,
)
from app.infra.idgen import id_generator
from app.infra.store import InMemoryStore, github_repository_identity


class PostgresRecordStore(InMemoryStore):
    """API-compatible record store; imports legacy snapshots once, then writes deltas."""

    supports_targeted_mutations = True

    def __init__(self) -> None:
        dsn = get_settings().postgres_dsn
        if not dsn:
            raise RuntimeError("RESEARCHFORGE_POSTGRES_DSN is required for the PostgreSQL store")
        settings = get_settings()
        self._engine = create_engine(
            make_url(dsn).set(drivername="postgresql+psycopg"),
            pool_pre_ping=True,
            pool_size=settings.postgres_pool_size,
            max_overflow=settings.postgres_max_overflow,
            pool_timeout=settings.postgres_pool_timeout_seconds,
            pool_recycle=settings.postgres_pool_recycle_seconds,
        )
        self._query_index = Table(
            "store_query_index", MetaData(),
            Column("collection"), Column("entity_id"), Column("workspace_id"), Column("task_id"),
            Column("status"), Column("kind"), Column("created_at"), Column("search_text"), Column("body", JSONB),
        )
        self._query_index_present: bool | None = None
        self._baseline: dict = {}
        self._mutex = RLock()
        self._unit_session: Session | None = None
        self._initializing = True
        with self._engine.begin() as conn:
            conn.execute(text("select pg_advisory_xact_lock(72410629)"))
            self._migration_summary = MigrationRunner().apply(conn.connection.driver_connection)
        self._import_legacy_snapshot()
        super().__init__()
        self._initializing = False
        self._persist()

    @staticmethod
    def _persistence_enabled_for_backend(settings) -> bool:
        return settings.persistence_enabled and settings.store_backend == "postgres"

    def _import_legacy_snapshot(self) -> None:
        with Session(self._engine) as session, session.begin():
            session.execute(text("select pg_advisory_xact_lock(72410629)"))
            exists = session.scalar(text("select to_regclass('public.app_snapshots')"))
            if not exists:
                return
            legacy = session.scalar(text("select body from app_snapshots where id = 'default'"))
            if not isinstance(legacy, dict):
                return

            # Older deployments stored the whole repository connection collection
            # inside app_snapshots. Migrate it into the typed table even when the
            # generic store already contains other collections.
            legacy_repositories = legacy.get("repository_connections") or {}
            if isinstance(legacy_repositories, dict):
                for key, body in legacy_repositories.items():
                    if not isinstance(body, dict) or session.get(
                        RepositoryConnectionRecord, str(key)
                    ) is not None:
                        continue
                    item = RepositoryConnectionResponse.model_validate(body)
                    session.add(
                        RepositoryConnectionRecord(
                            id=item.id,
                            workspace_id=item.workspace_id,
                            name=item.name,
                            provider=item.provider,
                            url=item.url,
                            local_path=item.local_path,
                            default_branch=item.default_branch,
                            credential_ref=item.credential_ref,
                            github_installation_id=item.github_installation_id,
                            github_owner=item.github_owner,
                            github_repository=item.github_repository,
                            status=item.status,
                            created_at=item.created_at,
                            updated_at=item.created_at,
                        )
                    )

            if session.scalar(select(StoreRecord.entity_id).limit(1)) is None:
                for (collection, key), body in flatten_snapshot(legacy).items():
                    if collection == "repository_connections":
                        continue
                    session.add(StoreRecord(collection=collection, entity_id=key, body=body))

    def _read_records(self, session: Session) -> dict:
        # Repository credentials and webhook identity use a typed table. All
        # other legacy collections remain readable during the staged cutover.
        return {
            (item.collection, item.entity_id): deepcopy(item.body)
            for item in session.scalars(
                select(StoreRecord).where(StoreRecord.collection != "repository_connections")
            )
        }

    @staticmethod
    def _repository_response(row: RepositoryConnectionRecord) -> RepositoryConnectionResponse:
        return RepositoryConnectionResponse(
            id=row.id,
            workspace_id=row.workspace_id,
            name=row.name,
            provider=row.provider,
            url=row.url,
            local_path=row.local_path,
            default_branch=row.default_branch,
            credential_ref=row.credential_ref,
            github_installation_id=row.github_installation_id,
            github_owner=row.github_owner,
            github_repository=row.github_repository,
            status=row.status,
            created_at=row.created_at,
        )

    def _read_typed_repositories(self, session: Session) -> dict[str, dict]:
        return {
            row.id: self._repository_response(row).model_dump(mode="json")
            for row in session.scalars(
                select(RepositoryConnectionRecord).order_by(
                    RepositoryConnectionRecord.created_at.desc(),
                    RepositoryConnectionRecord.id.desc(),
                )
            )
        }

    def _load_snapshot(self) -> None:
        if not self._persist_enabled:
            return
        with self._mutex:
            if self._unit_session is not None:
                self._baseline = self._read_records(self._unit_session)
            else:
                with Session(self._engine) as session:
                    self._baseline = self._read_records(session)
                    repositories = self._read_typed_repositories(session)
            if self._unit_session is not None:
                repositories = self._read_typed_repositories(self._unit_session)
            snapshot = inflate_records(self._baseline)
            snapshot["repository_connections"] = repositories
            self._restore_snapshot_data(
                snapshot,
                hydrate_artifacts=False,
            )

    def _read_entity(self, collection: str, entity_id: str, model):
        with Session(self._engine) as session:
            record = session.get(StoreRecord, (collection, entity_id))
            if record is None:
                return None
            return model.model_validate(deepcopy(record.body))

    def read_run(self, run_id: str) -> AgentRunResponse | None:
        return self._read_entity("runs", run_id, AgentRunResponse)

    def read_runs(self) -> list[AgentRunResponse]:
        return self._read_collection("runs", AgentRunResponse)

    def query_run_records(
        self,
        *,
        workspace_id: str | None = None,
        task_id: str | None = None,
        status: str | None = None,
        query: str | None = None,
        model_name: str | None = None,
        strategy_id: str | None = None,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
    ) -> list[AgentRunResponse]:
        """Use the PostgreSQL query projection for the run-record candidate set."""
        filters = {
            key: value
            for key, value in {
                "status": status,
                "task_id": task_id,
                "model_name": model_name,
                "agent_strategy_id": strategy_id,
            }.items()
            if value
        }
        # Fetch in bounded pages so the API does not silently cap totals at
        # 1,000 runs when building summaries or facets.
        items: list[AgentRunResponse] = []
        offset = 0
        while True:
            indexed = self.query_records(
                "runs",
                workspace_id=workspace_id,
                filters=filters,
                query=query,
                limit=1000,
                offset=offset,
            )
            items.extend(AgentRunResponse.model_validate(body) for body in indexed["items"])
            offset += len(indexed["items"])
            if offset >= int(indexed["total"] or 0) or not indexed["items"]:
                break
        filtered: list[AgentRunResponse] = []
        needle = (query or "").strip().lower()
        for run in items:
            if model_name and run.model_name != model_name:
                continue
            if strategy_id and run.agent_strategy_id != strategy_id:
                continue
            event_time = run.finished_at or run.started_at or run.created_at
            if from_time and event_time < from_time:
                continue
            if to_time and event_time > to_time:
                continue
            if needle:
                searchable = " ".join(
                    str(value or "")
                    for value in (
                        run.id,
                        run.task_id,
                        run.task_title,
                        run.repository_id,
                        run.project_name,
                        run.repo_path,
                        run.repository_url,
                        run.branch,
                        run.default_branch,
                        run.model_name,
                        run.agent_strategy_id,
                        run.error_summary,
                    )
                ).lower()
                if needle not in searchable:
                    continue
            filtered.append(run)
        return filtered

    def list_users(self, workspace_id: str | None = None) -> list[UserResponse]:
        items = self._read_collection("users", UserResponse)
        if workspace_id:
            items = [item for item in items if item.workspace_id == workspace_id]
        return sorted(items, key=lambda item: (item.created_at, item.id), reverse=True)

    def list_audit_logs(
        self,
        actor_id: str | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        decision: str | None = None,
    ) -> list[AuditLogResponse]:
        """Read audit records from PostgreSQL so multiple API workers stay consistent."""
        items = self._read_collection("audit_logs", AuditLogResponse)
        if actor_id:
            items = [item for item in items if item.actor_id == actor_id]
        if action:
            items = [item for item in items if item.action == action]
        if resource_type:
            items = [item for item in items if item.resource_type == resource_type]
        if resource_id:
            items = [item for item in items if item.resource_id == resource_id]
        if decision:
            items = [item for item in items if item.decision == decision]
        self.audit_logs = {item.id: item for item in items}
        return sorted(items, key=lambda item: (item.created_at, item.id), reverse=True)

    def query_users_page(
        self,
        *,
        workspace_id: str | None = None,
        status: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, object]:
        """Page users in PostgreSQL without materializing the full collection."""
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("INVALID_USER_QUERY")
        indexed = self._query_index_available()
        source = self._query_index if indexed else StoreRecord
        body = source.c.body if indexed else StoreRecord.body
        statement = select(body).where(
            source.c.collection == "users" if indexed else StoreRecord.collection == "users"
        )
        if workspace_id:
            statement = statement.where(
                source.c.workspace_id == workspace_id
                if indexed
                else StoreRecord.body["workspace_id"].as_string() == workspace_id
            )
        if status:
            statement = statement.where(
                source.c.status == status
                if indexed
                else StoreRecord.body["status"].as_string() == status
            )
        needle = (query or "").strip().lower()
        if needle:
            searchable = (
                body["id"].as_string(),
                body["name"].as_string(),
                body["email"].as_string(),
                body["role"].as_string(),
                body["workspace_id"].as_string(),
                body["status"].as_string(),
            )
            statement = statement.where(
                or_(*(func.lower(field).contains(needle, autoescape=True) for field in searchable))
            )
        ordering = (
            source.c.created_at.desc().nulls_last(), source.c.entity_id.desc()
        ) if indexed else (
            StoreRecord.body["created_at"].as_string().desc(), StoreRecord.entity_id.desc()
        )
        with Session(self._engine) as session, session.begin():
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            total = int(session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
            page = statement.order_by(*ordering).offset(offset).limit(limit)
            items = [UserResponse.model_validate(deepcopy(body)) for body in session.scalars(page)]
            return {"items": items, "total": total}

    def get_user(self, user_id: str) -> UserResponse | None:
        return self._read_entity("users", user_id, UserResponse)

    def _memory_records(self, collections: set[str], keys=None) -> dict[tuple[str, str], object]:
        """Build a small persistence delta without serializing the full store."""
        records: dict[tuple[str, str], object] = {}
        for collection in collections:
            value = getattr(self, collection, {})
            if keys is not None and collection in keys and isinstance(value, dict):
                value = {key: value[key] for key in keys[collection] if key in value}
            if collection == "events":
                for event_items in value.values():
                    for event in event_items:
                        body = event.model_dump(mode="json") if hasattr(event, "model_dump") else event
                        records[(collection, body["id"])] = deepcopy(body)
                continue
            if collection in {"cancel_requests", "pause_requests"}:
                records.update({(collection, key): True for key in value})
                continue
            if not isinstance(value, dict):
                continue
            for key, item in value.items():
                body = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                records[(collection, str(key))] = deepcopy(body)
        return records

    def _write_targeted_records(
        self,
        session: Session,
        baseline: dict[tuple[str, str], object],
        current: dict[tuple[str, str], object],
    ) -> dict[tuple[str, str], object]:
        """Persist only changed records while retaining field-level conflict checks."""
        persisted: dict[tuple[str, str], object] = {}
        for collection, key in sorted(baseline.keys() | current.keys()):
            identity = (collection, key)
            base, local = baseline.get(identity), current.get(identity)
            if base == local:
                continue
            row = session.get(StoreRecord, identity, with_for_update=True)
            remote = deepcopy(row.body) if row is not None else None
            merged = merge_record(base, local, remote, f"{collection}/{key}")
            if merged is None:
                if row is not None:
                    session.delete(row)
                continue
            if row is None:
                session.add(StoreRecord(collection=collection, entity_id=key, body=merged))
            else:
                row.body = merged
                row.version += 1
                row.updated_at = datetime.now(timezone.utc)
            persisted[identity] = deepcopy(merged)
        return persisted

    @contextmanager
    def _targeted_mutation(self, collections: set[str], keys=None):
        """Run a hot-path mutation without the global full-snapshot writer lock."""
        if self._unit_session is not None:
            yield
            return
        with self._mutex, Session(self._engine) as session, session.begin():
            original_keys = {collection: set(getattr(self, collection)) for collection in collections} if keys is not None else None
            baseline = self._memory_records(collections, keys)
            saved = {
                collection: deepcopy(getattr(self, collection)) if keys is None else {
                    key: deepcopy(getattr(self, collection)[key]) for key in keys.get(collection, set())
                    if key in getattr(self, collection)
                }
                for collection in collections
            }
            self._unit_session = session
            try:
                yield
                changed_keys = None if keys is None else {
                    collection: keys.get(collection, set()) | (set(getattr(self, collection)) - original_keys[collection])
                    for collection in collections
                }
                current = self._memory_records(collections, changed_keys)
                persisted = self._write_targeted_records(session, baseline, current)
                session.flush()
                self._baseline.update(persisted)
            except Exception:
                for collection, value in saved.items():
                    if keys is None:
                        setattr(self, collection, value)
                    else:
                        target = getattr(self, collection)
                        for key in set(target) - original_keys[collection]:
                            del target[key]
                        target.update(value)
                raise
            finally:
                self._unit_session = None

    def _lock_idempotency_key(self, namespace: str, value: str | None) -> None:
        """Serialize only retries for the same external delivery key."""
        if not value or self._unit_session is None:
            return
        self._unit_session.execute(
            text("select pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": f"researchforge:{namespace}:{value}"},
        )

    def create_user(self, request: CreateUserRequest) -> UserResponse:
        with self._targeted_mutation({"users", "audit_logs"}):
            return InMemoryStore.create_user(self, request)

    def update_user(self, user_id: str, request: UpdateUserRequest) -> UserResponse | None:
        item = self._read_entity("users", user_id, UserResponse)
        if item is not None:
            self.users[item.id] = item
        with self._targeted_mutation({"users", "audit_logs"}):
            return InMemoryStore.update_user(self, user_id, request)

    def upsert_model_config(self, model):
        with self._targeted_mutation({"model_configs", "audit_logs"}):
            return InMemoryStore.upsert_model_config(self, model)

    def create_preference_pair(self, request: CreatePreferencePairRequest):
        # The request can arrive on a fresh API process, so hydrate only the two
        # referenced runs instead of refreshing the entire store projection.
        for run_id in (request.chosen_run_id, request.rejected_run_id):
            run = self._read_entity("runs", run_id, AgentRunResponse)
            if run is not None:
                self.runs[run.id] = run
        with self._targeted_mutation({"preference_pairs", "audit_logs"}):
            return InMemoryStore.create_preference_pair(self, request)

    def update_preference_pair(self, pair_id: str, request: UpdatePreferencePairRequest):
        item = self._read_entity("preference_pairs", pair_id, PreferencePairResponse)
        if item is not None:
            self.preference_pairs[item.id] = item
        with self._targeted_mutation({"preference_pairs", "audit_logs"}):
            return InMemoryStore.update_preference_pair(self, pair_id, request)

    def _refresh_trace_context(self, request: CreateTraceDatasetItemRequest) -> None:
        run = self._read_entity("runs", request.agent_run_id, AgentRunResponse)
        if run is not None:
            self.runs[run.id] = run
        if request.agent_error_step_id:
            step = self._read_entity("steps", request.agent_error_step_id, AgentStep)
            if step is not None:
                self.steps[step.id] = step
        for item in self._read_collection(
            "trace_dataset_items",
            TraceDatasetItemResponse,
            {"agent_run_id": request.agent_run_id},
        ):
            self.trace_dataset_items[item.id] = item

    def create_trace_dataset_item(
        self,
        request: CreateTraceDatasetItemRequest,
    ) -> TraceDatasetItemResponse:
        self._refresh_trace_context(request)
        with self._targeted_mutation({"trace_dataset_items", "audit_logs"}):
            self._lock_idempotency_key("trace-dataset", request.agent_run_id)
            return InMemoryStore.create_trace_dataset_item(self, request)

    def update_trace_dataset_item(
        self,
        item_id: str,
        request: UpdateTraceDatasetItemRequest,
    ) -> TraceDatasetItemResponse | None:
        item = self._read_entity("trace_dataset_items", item_id, TraceDatasetItemResponse)
        if item is not None:
            self.trace_dataset_items[item.id] = item
        if request.agent_error_step_id and item is not None:
            step = self._read_entity("steps", request.agent_error_step_id, AgentStep)
            if step is not None:
                self.steps[step.id] = step
        with self._targeted_mutation({"trace_dataset_items", "audit_logs"}):
            return InMemoryStore.update_trace_dataset_item(self, item_id, request)

    def get_trace_dataset_item(self, item_id: str) -> TraceDatasetItemResponse | None:
        return self._read_entity("trace_dataset_items", item_id, TraceDatasetItemResponse)

    def list_trace_dataset_items(self, **filters) -> list[TraceDatasetItemResponse]:
        workspace_id = filters.pop("workspace_id", None)
        items = self._read_collection("trace_dataset_items", TraceDatasetItemResponse, filters)
        if workspace_id:
            tasks = {
                task.id: task.workspace_id
                for task in self._read_collection("tasks", TaskResponse)
            }
            runs = self._read_collection("runs", AgentRunResponse)
            run_tasks = {run.id: run.task_id for run in runs}
            items = [item for item in items if tasks.get(run_tasks.get(item.agent_run_id)) == workspace_id]
        return sorted(items, key=lambda item: (item.created_at, item.id), reverse=True)

    def query_trace_dataset_items_page(
        self,
        *,
        workspace_id: str | None = None,
        quality_label: str | None = None,
        trace_type: str | None = None,
        use_case: str | None = None,
        failure_type: str | None = None,
        status: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, object]:
        """Page Trace records with workspace joins executed by PostgreSQL."""
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("INVALID_TRACE_QUERY")
        indexed = self._query_index_available()
        if indexed:
            trace = self._query_index.alias("trace_items")
            run = self._query_index.alias("trace_runs")
            task = self._query_index.alias("trace_tasks")
            body = trace.c.body
            statement = (
                select(body)
                .select_from(trace)
                .join(
                    run,
                    (run.c.collection == "runs")
                    & (run.c.entity_id == body["agent_run_id"].as_string()),
                )
                .join(
                    task,
                    (task.c.collection == "tasks")
                    & (task.c.entity_id == run.c.task_id),
                )
                .where(trace.c.collection == "trace_dataset_items")
            )
        else:
            trace = aliased(StoreRecord)
            run = aliased(StoreRecord)
            task = aliased(StoreRecord)
            body = trace.body
            statement = (
                select(body)
                .select_from(trace)
                .join(
                    run,
                    (run.collection == "runs")
                    & (run.entity_id == body["agent_run_id"].as_string()),
                )
                .join(
                    task,
                    (task.collection == "tasks")
                    & (task.entity_id == run.body["task_id"].as_string()),
                )
                .where(trace.collection == "trace_dataset_items")
            )
        if workspace_id:
            statement = statement.where(
                task.c.workspace_id == workspace_id
                if indexed
                else task.body["workspace_id"].as_string() == workspace_id
            )
        for field, value in (
            ("quality_label", quality_label),
            ("trace_type", trace_type),
            ("use_case", use_case),
            ("failure_type", failure_type),
            ("status", status),
        ):
            if value:
                statement = statement.where(body[field].as_string() == value)
        needle = (query or "").strip().lower()
        if needle:
            searchable = (
                body["id"].as_string(),
                body["agent_run_id"].as_string(),
                body["quality_label"].as_string(),
                body["trace_type"].as_string(),
                body["use_case"].as_string(),
                body["failure_type"].as_string(),
                body["root_cause"].as_string(),
                body["notes"].as_string(),
            )
            statement = statement.where(
                or_(*(func.lower(field).contains(needle, autoescape=True) for field in searchable))
            )
        ordering = (
            trace.c.created_at.desc().nulls_last(), trace.c.entity_id.desc()
        ) if indexed else (
            trace.body["created_at"].as_string().desc(), trace.entity_id.desc()
        )
        with Session(self._engine) as session, session.begin():
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            total = int(session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
            page = statement.order_by(*ordering).offset(offset).limit(limit)
            items = [
                TraceDatasetItemResponse.model_validate(deepcopy(item_body))
                for item_body in session.scalars(page)
            ]
            return {"items": items, "total": total}

    def list_preference_pairs(
        self,
        task_id: str | None = None,
        use_case: str | None = None,
        workspace_id: str | None = None,
    ) -> list[PreferencePairResponse]:
        items = self._read_collection("preference_pairs", PreferencePairResponse)
        if task_id:
            items = [item for item in items if item.task_id == task_id]
        if use_case:
            items = [item for item in items if item.use_case == use_case]
        if workspace_id:
            tasks = {
                task.id: task.workspace_id
                for task in self._read_collection("tasks", TaskResponse)
            }
            items = [item for item in items if tasks.get(item.task_id) == workspace_id]
        return sorted(items, key=lambda item: (item.created_at, item.id), reverse=True)

    def read_evaluation_run(self, evaluation_run_id: str) -> EvaluationRunResponse | None:
        return self._read_entity("evaluation_runs", evaluation_run_id, EvaluationRunResponse)

    def read_job(self, job_id: str) -> JobResponse | None:
        return self._read_entity("jobs", job_id, JobResponse)

    def read_artifact(self, artifact_id: str) -> Artifact | None:
        artifact = self._read_entity("artifacts", artifact_id, Artifact)
        return self._hydrate_artifact_content(artifact) if artifact is not None else None

    def _read_collection(self, collection: str, model, filters: dict[str, str | None] | None = None):
        statement = select(StoreRecord.body).where(StoreRecord.collection == collection)
        for field, value in (filters or {}).items():
            if value is not None:
                statement = statement.where(StoreRecord.body[field].as_string() == value)
        statement = statement.order_by(
            StoreRecord.body["created_at"].as_string().desc(),
            StoreRecord.entity_id.desc(),
        )
        with Session(self._engine) as session:
            return [
                model.model_validate(deepcopy(body))
                for body in session.scalars(statement)
            ]

    def read_evaluation_runs(self) -> list[EvaluationRunResponse]:
        return self._read_collection("evaluation_runs", EvaluationRunResponse)

    def read_steps(self, run_id: str) -> list[AgentStep]:
        return sorted(
            self._read_collection("steps", AgentStep, {"run_id": run_id}),
            key=lambda item: item.step_index,
        )

    def read_tool_calls(self, run_id: str) -> list[ToolCall]:
        return self._read_collection("tool_calls", ToolCall, {"run_id": run_id})

    def read_artifacts(self, run_id: str) -> list[Artifact]:
        artifacts = self._read_collection("artifacts", Artifact, {"run_id": run_id})
        return [self._hydrate_artifact_content(artifact) for artifact in artifacts]

    def read_jobs(
        self,
        kind: str | None = None,
        status: str | None = None,
        task_id: str | None = None,
        resource_id: str | None = None,
    ) -> list[JobResponse]:
        return self._read_collection(
            "jobs",
            JobResponse,
            {
                "kind": kind,
                "status": status,
                "task_id": task_id,
                "resource_id": resource_id,
            },
        )

    def _write_records(self, session: Session, current: dict) -> None:
        # Lock writers while checking the baseline, including insertion races.
        session.execute(text("select pg_advisory_xact_lock(72410630)"))
        for collection, key in sorted(self._baseline.keys() | current.keys()):
            identity = (collection, key)
            base, local = self._baseline.get(identity), current.get(identity)
            if base == local:
                continue
            row = session.get(StoreRecord, identity, with_for_update=True)
            remote = row.body if row else None
            merged = merge_record(base, local, remote, f"{collection}/{key}")
            if merged is None:
                if row:
                    session.delete(row)
            elif row:
                row.body = merged
                row.version += 1
                row.updated_at = datetime.now(timezone.utc)
            else:
                session.add(StoreRecord(collection=collection, entity_id=key, body=merged))

    @staticmethod
    def _without_typed_records(records: dict) -> dict:
        return {
            key: value
            for key, value in records.items()
            if key[0] != "repository_connections"
        }

    def _upsert_repository(self, item: RepositoryConnectionResponse) -> None:
        with self._mutex, Session(self._engine) as session, session.begin():
            workspace = self.workspaces.get(item.workspace_id)
            if workspace is None:
                raise ValueError("Workspace not found")
            # Fresh stores seed compatibility workspaces after migrations run.
            session.execute(text(
                "insert into workspaces (id, name, owner_id, status, created_at) "
                "values (:id, :name, :owner_id, :status, :created_at) on conflict (id) do nothing"
            ), {"id": workspace.id, "name": workspace.name, "owner_id": workspace.owner_id,
                "status": workspace.status, "created_at": workspace.created_at})
            row = session.get(RepositoryConnectionRecord, item.id, with_for_update=True)
            fields = {
                "workspace_id": item.workspace_id,
                "name": item.name,
                "provider": item.provider,
                "url": item.url,
                "local_path": item.local_path,
                "default_branch": item.default_branch,
                "credential_ref": item.credential_ref,
                "github_installation_id": item.github_installation_id,
                "github_owner": item.github_owner,
                "github_repository": item.github_repository,
                "status": item.status,
                "updated_at": datetime.now(timezone.utc),
            }
            if row is None:
                session.add(RepositoryConnectionRecord(id=item.id, created_at=item.created_at, **fields))
                return
            for key, value in fields.items():
                setattr(row, key, value)

    def _assert_repository_connection_unique(
        self,
        request: CreateRepositoryConnectionRequest,
        *,
        exclude_id: str | None = None,
    ) -> None:
        if request.provider.strip().casefold() != "github":
            return
        identity = github_repository_identity(
            request.url,
            request.github_owner,
            request.github_repository,
        )
        if identity is None:
            return
        statement = select(RepositoryConnectionRecord).where(
            func.lower(RepositoryConnectionRecord.provider) == "github",
            func.lower(RepositoryConnectionRecord.github_owner) == identity[0],
            func.lower(RepositoryConnectionRecord.github_repository) == identity[1],
        )
        if exclude_id:
            statement = statement.where(RepositoryConnectionRecord.id != exclude_id)
        with Session(self._engine) as session:
            for existing in session.scalars(statement):
                if (
                    request.github_installation_id is None
                    or existing.github_installation_id is None
                    or existing.github_installation_id == request.github_installation_id
                ):
                    raise ValueError("GITHUB_REPOSITORY_ALREADY_CONNECTED")

    def create_repository_connection(
        self,
        request: CreateRepositoryConnectionRequest,
        workspace_id: str = "workspace_default",
    ) -> RepositoryConnectionResponse:
        if workspace_id not in self.workspaces:
            raise ValueError("Workspace not found")
        request = self._normalize_repository_connection_request(request)
        self._assert_repository_connection_unique(request)
        item = RepositoryConnectionResponse(
            id=id_generator.next("repo_conn"),
            workspace_id=workspace_id,
            name=request.name,
            provider=request.provider,
            url=request.url,
            local_path=request.local_path,
            default_branch=request.default_branch,
            credential_ref=request.credential_ref,
            github_installation_id=request.github_installation_id,
            github_owner=request.github_owner,
            github_repository=request.github_repository,
        )
        self._upsert_repository(item)
        self.repository_connections[item.id] = item
        with self._targeted_mutation({"audit_logs"}):
            self.add_audit_log(
                action="repository_connection.create",
                resource_type="repository_connection",
                resource_id=item.id,
                decision="allow",
                actor_id="operator",
                detail_json={"provider": item.provider, "name": item.name},
            )
        return item

    def get_repository_connection(self, repository_id: str) -> RepositoryConnectionResponse | None:
        with Session(self._engine) as session:
            row = session.get(RepositoryConnectionRecord, repository_id)
            if row is None:
                return None
            item = self._repository_response(row)
            self.repository_connections[item.id] = item
            return item

    def update_repository_connection(
        self,
        repository_id: str,
        request: UpdateRepositoryConnectionRequest,
    ) -> RepositoryConnectionResponse | None:
        item = self.get_repository_connection(repository_id)
        if item is None:
            return None
        changes = request.model_dump(exclude_unset=True)
        workspace_id = changes.get("workspace_id")
        if workspace_id is not None and workspace_id not in self.workspaces:
            raise ValueError("Workspace not found")
        candidate = item.model_copy(update=changes)
        normalized = self._normalize_repository_connection_request(
            CreateRepositoryConnectionRequest(
                name=candidate.name,
                workspace_id=candidate.workspace_id,
                provider=candidate.provider,
                url=candidate.url,
                local_path=candidate.local_path,
                default_branch=candidate.default_branch,
                credential_ref=candidate.credential_ref,
                github_installation_id=candidate.github_installation_id,
                github_owner=candidate.github_owner,
                github_repository=candidate.github_repository,
            )
        )
        self._assert_repository_connection_unique(normalized, exclude_id=item.id)
        for key, value in {
            **changes,
            "github_installation_id": normalized.github_installation_id,
            "github_owner": normalized.github_owner,
            "github_repository": normalized.github_repository,
        }.items():
            if value is not None or key.startswith("github_"):
                setattr(item, key, value)
        self._upsert_repository(item)
        self.repository_connections[item.id] = item
        with self._targeted_mutation({"audit_logs"}):
            self.add_audit_log(
                action="repository_connection.update",
                resource_type="repository_connection",
                resource_id=item.id,
                decision="allow",
                actor_id="operator",
                detail_json={"changed_fields": sorted(changes)},
            )
        return item

    def list_repository_connections(
        self,
        workspace_id: str | None = None,
        provider: str | None = None,
    ) -> list[RepositoryConnectionResponse]:
        statement = select(RepositoryConnectionRecord)
        if workspace_id:
            statement = statement.where(RepositoryConnectionRecord.workspace_id == workspace_id)
        if provider:
            statement = statement.where(RepositoryConnectionRecord.provider == provider)
        statement = statement.order_by(
            RepositoryConnectionRecord.created_at.desc(),
            RepositoryConnectionRecord.id.desc(),
        )
        with Session(self._engine) as session:
            items = [self._repository_response(row) for row in session.scalars(statement)]
        self.repository_connections = {item.id: item for item in items}
        return items

    def _persist(self) -> None:
        if not self._persist_enabled or self._initializing or self._unit_session is not None:
            return
        with self._mutex:
            current = self._without_typed_records(flatten_snapshot(self._snapshot_data()))
            try:
                with Session(self._engine) as session, session.begin():
                    self._write_records(session, current)
            except StoreConflictError:
                self._load_snapshot()
                raise
            # Preserve local model references held by the running agent.
            self._baseline = deepcopy(current)

    @contextmanager
    def _atomic_creation(self):
        with self._mutex, Session(self._engine) as session, session.begin():
            session.execute(text("select pg_advisory_xact_lock(72410630)"))
            self._unit_session = session
            try:
                self._load_snapshot()
                yield
                current = self._without_typed_records(flatten_snapshot(self._snapshot_data()))
                self._write_records(session, current)
                session.flush()
            except Exception:
                self._restore_snapshot_data(
                    inflate_records(self._baseline),
                    hydrate_artifacts=False,
                )
                raise
            finally:
                self._unit_session = None
            self._baseline = deepcopy(current)

    def create_task(self, request):
        with self._targeted_mutation({"tasks", "audit_logs"}):
            return InMemoryStore.create_task(self, request)

    def _runtime_keys(self, run_id, collections, entity=None):
        keys = {collection: set() for collection in collections}
        if run_id is not None:
            if "events" in keys:
                keys["events"] = {run_id}
            if "runs" in keys:
                keys["runs"] = {run_id}
            if "tasks" in keys:
                keys["tasks"] = {self.runs[run_id].task_id}
        if entity:
            collection, key = entity
            keys[collection].add(key)
        return keys

    def update_run(self, run_id, *args, **kwargs):
        collections = {"runs", "tasks", "events", "audit_logs", "model_canaries", "model_configs"}
        keys = self._runtime_keys(run_id, collections)
        canary_id = self.runs[run_id].metrics.get("canary_id")
        if canary_id:
            # Canary monitoring can modify model routing; retain its existing scope.
            with self._targeted_mutation(collections):
                return InMemoryStore.update_run(self, run_id, *args, **kwargs)
        with self._targeted_mutation(collections, keys):
            return InMemoryStore.update_run(self, run_id, *args, **kwargs)

    def add_step(self, run_id, *args, **kwargs):
        collections = {"steps", "events"}
        with self._targeted_mutation(collections, self._runtime_keys(run_id, collections)):
            return InMemoryStore.add_step(self, run_id, *args, **kwargs)

    def record_run_usage(self, run_id, token_delta, cost_delta):
        with self._targeted_mutation({"runs"}, {"runs": {run_id}}):
            return InMemoryStore.record_run_usage(self, run_id, token_delta, cost_delta)

    def record_model_usage(self, **kwargs):
        collections = {"model_usage_ledger", "audit_logs"}
        with self._targeted_mutation(collections, {collection: set() for collection in collections}):
            return InMemoryStore.record_model_usage(self, **kwargs)

    def finish_step(self, step_id, *args, **kwargs):
        collections = {"steps", "events"}
        keys = self._runtime_keys(self.steps[step_id].run_id, collections, ("steps", step_id))
        with self._targeted_mutation(collections, keys):
            return InMemoryStore.finish_step(self, step_id, *args, **kwargs)

    def add_tool_call(self, tool_call):
        collections = {"tool_calls", "events"}
        keys = self._runtime_keys(tool_call.run_id, collections, ("tool_calls", tool_call.id))
        with self._targeted_mutation(collections, keys):
            return InMemoryStore.add_tool_call(self, tool_call)

    def add_artifact(self, artifact):
        collections = {"artifacts", "events"}
        keys = self._runtime_keys(artifact.run_id, collections, ("artifacts", artifact.id))
        with self._targeted_mutation(collections, keys):
            return InMemoryStore.add_artifact(self, artifact)

    def add_event(self, event_type, task_id, run_id, payload, step_id=None):
        if event_type in {"run.completed", "run.failed", "run.cancelled", "run.paused"}:
            # Terminal hooks may mutate additional collections.
            return InMemoryStore.add_event(self, event_type, task_id, run_id, payload, step_id)
        collections = {"events"}
        with self._targeted_mutation(collections, self._runtime_keys(run_id, collections)):
            return InMemoryStore.add_event(self, event_type, task_id, run_id, payload, step_id)

    def add_audit_log(self, *args, **kwargs):
        with self._targeted_mutation({"audit_logs"}, {"audit_logs": set()}):
            return InMemoryStore.add_audit_log(self, *args, **kwargs)

    def create_run(self, task_id, agent_strategy_id, policy_version_id, model_name):
        with self._targeted_mutation({"tasks", "runs", "events", "audit_logs"}):
            return InMemoryStore.create_run(
                self,
                task_id,
                agent_strategy_id,
                policy_version_id,
                model_name,
            )

    def create_run_with_job(
        self,
        task_id,
        agent_strategy_id,
        policy_version_id,
        model_name,
        idempotency_key=None,
    ):
        with self._targeted_mutation({"tasks", "runs", "jobs", "events", "audit_logs"}):
            self._lock_idempotency_key("agent-run", idempotency_key)
            if idempotency_key:
                for existing_job in self.list_jobs(kind="agent_run", task_id=task_id):
                    if existing_job.metadata.get("idempotency_key") != idempotency_key:
                        continue
                    existing_run = self.read_run(existing_job.resource_id)
                    if existing_run is not None:
                        return existing_run, existing_job, False
            run = InMemoryStore.create_run(
                self,
                task_id,
                agent_strategy_id,
                policy_version_id,
                model_name,
            )
            job = InMemoryStore.create_job(
                self,
                kind="agent_run",
                resource_id=run.id,
                task_id=task_id,
                metadata={"idempotency_key": idempotency_key} if idempotency_key else None,
            )
            return run, job, True

    def create_job_with_idempotency(
        self,
        kind,
        resource_id,
        *,
        idempotency_key,
        task_id=None,
        metadata=None,
        parent_job_id=None,
        retry_of_job_id=None,
    ):
        with self._targeted_mutation({"jobs", "audit_logs"}):
            self._lock_idempotency_key("job", idempotency_key)
            if idempotency_key:
                for existing in self.list_jobs(kind=kind, resource_id=resource_id):
                    if existing.metadata.get("idempotency_key") == idempotency_key:
                        return existing, False
            job_metadata = dict(metadata or {})
            if idempotency_key:
                job_metadata["idempotency_key"] = idempotency_key
            job = InMemoryStore.create_job(
                self,
                kind=kind,
                resource_id=resource_id,
                task_id=task_id,
                metadata=job_metadata,
                parent_job_id=parent_job_id,
                retry_of_job_id=retry_of_job_id,
            )
            return job, True

    def update_job(self, job_id, status, error_summary=None, result_json=None, metadata=None):
        # Read the current durable job before updating, so API dispatch metadata
        # cannot race a worker's stale full snapshot.
        with self._mutex:
            item = self.read_job(job_id)
            if item is not None:
                self.jobs[job_id] = item
            with self._targeted_mutation({"jobs", "audit_logs"}):
                return InMemoryStore.update_job(
                    self, job_id, status, error_summary, result_json, metadata,
                )

    def is_cancel_requested(self, run_id: str) -> bool:
        with Session(self._engine) as session:
            return session.get(StoreRecord, ("cancel_requests", run_id)) is not None

    def query_records(self, collection, *, workspace_id=None, filters=None, query=None, limit=50, offset=0):
        from app.infra.queries import SEARCH_FIELDS
        if collection not in SEARCH_FIELDS or not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("INVALID_RECORD_QUERY")
        index = self._query_index_available()
        source = self._query_index
        if index:
            statement = select(source.c.body).where(source.c.collection == collection)
        else:
            statement = select(StoreRecord.body).where(StoreRecord.collection == collection)
        if workspace_id:
            if collection == "tasks":
                statement = statement.where(source.c.workspace_id == workspace_id if index else StoreRecord.body["workspace_id"].as_string() == workspace_id)
            else:
                if index:
                    task_index = self._query_index.alias("task_index")
                    statement = statement.where(source.c.task_id.in_(select(task_index.c.entity_id).where(task_index.c.collection == "tasks", task_index.c.workspace_id == workspace_id)))
                    task = None
                else:
                    task = aliased(StoreRecord)
                    statement = statement.where(StoreRecord.body["task_id"].as_string().in_(
                        select(task.entity_id).where(task.collection == "tasks", task.body["workspace_id"].as_string() == workspace_id)))
        for key, expected in (filters or {}).items():
            if key not in {"status", "type", "task_id", "model_name", "agent_strategy_id"}:
                raise ValueError("INVALID_QUERY_FILTER")
            if expected is not None:
                query_key = "kind" if key == "type" else key
                if index and query_key in {"model_name", "agent_strategy_id"}:
                    statement = statement.where(source.c.body[query_key].as_string() == expected)
                else:
                    statement = statement.where(
                        getattr(source.c, query_key) == expected
                        if index
                        else StoreRecord.body[key].as_string() == expected
                    )
        needle = (query or "").strip().lower()
        if needle:
            statement = statement.where(source.c.search_text.contains(needle, autoescape=True) if index else or_(*(func.lower(StoreRecord.body[key].as_string()).contains(needle, autoescape=True) for key in SEARCH_FIELDS[collection])))
        with Session(self._engine) as session, session.begin():
            # Count and page must use the same snapshot during concurrent writes.
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            total = session.scalar(select(func.count()).select_from(statement.subquery()))
            page = statement.order_by(source.c.created_at.desc().nulls_last(), source.c.entity_id.desc()).offset(offset).limit(limit) if index else statement.order_by(StoreRecord.body["created_at"].as_string().desc(), StoreRecord.entity_id.desc()).offset(offset).limit(limit)
            return {"items": list(session.scalars(page)), "total": total}

    def _query_index_available(self):
        if self._query_index_present is not None:
            return self._query_index_present
        with Session(self._engine) as session:
            self._query_index_present = bool(
                session.scalar(text("select to_regclass('public.store_query_index')"))
            )
        return self._query_index_present

    def is_pause_requested(self, run_id: str) -> bool:
        with Session(self._engine) as session:
            return session.get(StoreRecord, ("pause_requests", run_id)) is not None

    def describe_storage(self) -> dict[str, object]:
        return {
            **super().describe_storage(),
            "mode": "typed_operational_postgres_plus_legacy_records",
            "typed_repository_connections": True,
            "postgres_migrations": self._migration_summary,
        }
