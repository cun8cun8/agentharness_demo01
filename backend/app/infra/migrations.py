from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


class MigrationRunner:
    def __init__(self, migrations_path: str | Path | None = None) -> None:
        self.migrations_path = (
            Path(migrations_path)
            if migrations_path is not None
            else Path(__file__).resolve().parents[2] / "migrations"
        )

    def apply(self, connection: Any) -> dict[str, object]:
        scripts = self._scripts()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                create table if not exists schema_migrations (
                    filename text primary key,
                    checksum text not null,
                    applied_at timestamptz not null default now()
                )
                """
            )
            cursor.execute("select filename, checksum from schema_migrations order by filename")
            applied_rows = cursor.fetchall() or []
            applied_checksums = {
                str(row[0]): str(row[1])
                for row in applied_rows
                if row and row[0]
            }
            applied_files: list[str] = []
            for script in scripts:
                checksum = hashlib.sha256(script["sql"].encode("utf-8")).hexdigest()
                previous_checksum = applied_checksums.get(script["name"])
                if previous_checksum == checksum:
                    continue
                if previous_checksum and previous_checksum != checksum:
                    raise RuntimeError(f"Migration changed after being applied: {script['name']}")
                cursor.execute(script["sql"])
                cursor.execute(
                    """
                    insert into schema_migrations (filename, checksum, applied_at)
                    values (%s, %s, now())
                    on conflict (filename)
                    do update set checksum = excluded.checksum, applied_at = excluded.applied_at
                    """,
                    (script["name"], checksum),
                )
                applied_files.append(script["name"])
        return {
            "path": str(self.migrations_path),
            "available": len(scripts),
            "applied": len(applied_files),
            "recorded": len(applied_checksums) + len(applied_files),
            "applied_files": applied_files,
        }

    def _scripts(self) -> list[dict[str, str]]:
        if not self.migrations_path.exists():
            return []
        scripts: list[dict[str, str]] = []
        for path in sorted(self.migrations_path.glob("*.sql")):
            sql = path.read_text(encoding="utf-8").strip()
            if not sql:
                continue
            scripts.append({"name": path.name, "sql": sql})
        return scripts
