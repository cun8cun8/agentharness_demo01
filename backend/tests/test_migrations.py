from app.infra.migrations import MigrationRunner


class FakeCursor:
    def __init__(self, connection) -> None:
        self.connection = connection
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        return False

    def execute(self, sql: str, params=None) -> None:
        self.connection.executed.append((sql.strip(), params))
        if "select filename, checksum from schema_migrations" in sql:
            self.rows = list(self.connection.applied_rows)

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self) -> None:
        self.executed = []
        self.applied_rows = []

    def cursor(self):
        return FakeCursor(self)


def test_migration_runner_applies_unseen_sql_files(tmp_path) -> None:
    (tmp_path / "001_create_thing.sql").write_text("create table thing (id text primary key);", encoding="utf-8")
    (tmp_path / "002_add_name.sql").write_text("alter table thing add column name text;", encoding="utf-8")
    connection = FakeConnection()

    summary = MigrationRunner(tmp_path).apply(connection)

    assert summary["available"] == 2
    assert summary["applied"] == 2
    assert summary["applied_files"] == ["001_create_thing.sql", "002_add_name.sql"]
    executed_sql = "\n".join(sql for sql, _ in connection.executed)
    assert "create table if not exists schema_migrations" in executed_sql
    assert "create table thing" in executed_sql
    assert "alter table thing add column name text" in executed_sql
    assert sum(1 for _, params in connection.executed if params and params[0].endswith(".sql")) == 2


def test_migration_runner_skips_matching_applied_files(tmp_path) -> None:
    migration = tmp_path / "001_create_thing.sql"
    sql = "create table thing (id text primary key);"
    migration.write_text(sql, encoding="utf-8")
    connection = FakeConnection()
    first = MigrationRunner(tmp_path).apply(connection)
    connection.applied_rows = [
        (first["applied_files"][0], connection.executed[-1][1][1]),
    ]
    connection.executed = []

    second = MigrationRunner(tmp_path).apply(connection)

    assert second["available"] == 1
    assert second["applied"] == 0
    executed_sql = "\n".join(sql for sql, _ in connection.executed)
    assert "select filename, checksum from schema_migrations" in executed_sql
    assert "create table thing" not in executed_sql


def test_operational_projection_migration_covers_training_and_billing_records() -> None:
    migration = (MigrationRunner().migrations_path / "006_operational_projections.sql").read_text(encoding="utf-8")
    assert "operational_training_jobs" in migration
    assert "operational_model_usage_ledger" in migration
    assert "operational_model_billing_reconciliations" in migration
    assert "operational_model_billing_imports" in migration
    assert "store_records_operational_projection_trigger" in migration


def test_repository_connections_migration_uses_typed_github_identity() -> None:
    migration = (MigrationRunner().migrations_path / "007_repository_connections_typed.sql").read_text(encoding="utf-8")
    assert "alter table repository_connections" in migration
    assert "github_installation_id" in migration
    assert "uq_repository_connections_github_installation" in migration
    assert "regexp_replace" in migration
    assert "insert into workspaces" in migration
    assert "jsonb_each" in migration


def test_run_query_projection_migration_indexes_run_context_fields() -> None:
    migration = (MigrationRunner().migrations_path / "008_run_query_projection_search.sql").read_text(encoding="utf-8")
    assert "researchforge_refresh_query_index" in migration
    assert "project_name" in migration
    assert "agent_strategy_id" in migration
    assert "store_query_index" in migration
