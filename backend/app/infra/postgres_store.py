from __future__ import annotations

import json
from typing import Any

from app.config import get_settings
from app.infra.migrations import MigrationRunner
from app.infra.store import InMemoryStore


class PostgresSnapshotStore(InMemoryStore):
    """PostgreSQL-backed Store using one JSONB snapshot for the full P0 model.

    The P0 API already owns the in-process data model. Persisting that model as a
    versioned JSONB snapshot gives the platform real PostgreSQL durability now,
    while the table-oriented migration files remain the target for later query
    optimization and worker fan-out.
    """

    snapshot_id = "default"

    def __init__(self) -> None:
        self._migration_runner = MigrationRunner()
        self._schema_ready = False
        self._migration_summary: dict[str, object] = {
            "path": str(self._migration_runner.migrations_path),
            "available": 0,
            "applied": 0,
            "applied_files": [],
        }
        super().__init__()

    @staticmethod
    def _persistence_enabled_for_backend(settings) -> bool:
        return settings.persistence_enabled and settings.store_backend == "postgres"

    def describe_storage(self) -> dict[str, object]:
        return {
            **super().describe_storage(),
            "postgres_migrations": self._migration_summary,
            "snapshot_id": self.snapshot_id,
        }

    def _load_snapshot(self) -> None:
        if not self._persist_enabled:
            return
        self._ensure_configured()
        try:
            with self._connect() as connection:
                self._ensure_schema(connection)
                with connection.cursor() as cursor:
                    cursor.execute(
                        "select body from app_snapshots where id = %s",
                        (self.snapshot_id,),
                    )
                    row = cursor.fetchone()
        except Exception as exc:
            raise RuntimeError(f"Unable to load PostgreSQL store snapshot: {exc}") from exc
        if not row:
            return
        body = row[0]
        if isinstance(body, str):
            body = json.loads(body)
        self._restore_snapshot_data(body)

    def _persist(self) -> None:
        if not self._persist_enabled:
            return
        self._ensure_configured()
        try:
            from psycopg.types.json import Jsonb

            with self._connect() as connection:
                self._ensure_schema(connection)
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        insert into app_snapshots (id, body, updated_at)
                        values (%s, %s, now())
                        on conflict (id)
                        do update set body = excluded.body, updated_at = excluded.updated_at
                        """,
                        (self.snapshot_id, Jsonb(self._snapshot_data())),
                    )
        except Exception as exc:
            raise RuntimeError(f"Unable to persist PostgreSQL store snapshot: {exc}") from exc

    def _connect(self) -> Any:
        self._ensure_configured()
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError(
                "PostgreSQL store requires the optional psycopg dependency. "
                "Install backend/requirements.txt first."
            ) from exc
        return psycopg.connect(get_settings().postgres_dsn, autocommit=True)

    def _ensure_schema(self, connection: Any) -> None:
        if self._schema_ready:
            return
        self._migration_summary = self._migration_runner.apply(connection)
        self._ensure_snapshot_table(connection)
        self._schema_ready = True

    @staticmethod
    def _ensure_snapshot_table(connection: Any) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                create table if not exists app_snapshots (
                    id text primary key,
                    body jsonb not null,
                    updated_at timestamptz not null default now()
                )
                """
            )

    @staticmethod
    def _ensure_configured() -> None:
        if not get_settings().postgres_dsn:
            raise RuntimeError(
                "RESEARCHFORGE_POSTGRES_DSN is required when "
                "RESEARCHFORGE_STORE_BACKEND=postgres."
            )
