"""Restore a consistent PostgreSQL dump into a new disposable database and verify it."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


def digest_records(connection):
    digest = hashlib.sha256()
    counts = {}
    with connection.cursor(name="restore_drill_records") as cursor:
        cursor.execute("select collection, entity_id, body from store_records order by collection, entity_id")
        for collection, entity_id, body in cursor:
            payload = json.dumps([collection, entity_id, body], sort_keys=True, ensure_ascii=True, separators=(",", ":"))
            digest.update(payload.encode() + b"\n")
            counts[collection] = counts.get(collection, 0) + 1
    return {"sha256": digest.hexdigest(), "collections": counts, "record_count": sum(counts.values())}


def pg_tool(command, dsn, *, stdin=None, stdout=None, container=None):
    info = conninfo_to_dict(dsn)
    env = dict(os.environ)
    values = {"PGHOST": info.get("host", "localhost"), "PGPORT": info.get("port", "5432"),
              "PGUSER": info.get("user", "postgres"), "PGPASSWORD": info.get("password", ""), "PGDATABASE": info["dbname"],
              "PGCONNECT_TIMEOUT": "10", "PGSSLMODE": info.get("sslmode", "prefer")}
    if container:
        values.update(PGHOST=os.getenv("RESEARCHFORGE_DB_TOOLS_HOST", "127.0.0.1"),
                      PGPORT=os.getenv("RESEARCHFORGE_DB_TOOLS_PORT", "5432"))
    env.update(values)
    args = command
    if container:
        args = ["docker", "exec", "-i", *[part for key in values for part in ("--env", key)], container, *command]
    result = subprocess.run(args, stdin=stdin, stdout=stdout, stderr=subprocess.PIPE, env=env, timeout=3600, check=False)
    if result.returncode:
        raise RuntimeError(f"{command[0].upper()}_FAILED")


def restore_drill(dsn, output_dir, container=None):
    started = time.monotonic()
    name = "rf_restore_drill_" + uuid4().hex
    root = Path(output_dir).resolve() / name
    root.mkdir(parents=True, exist_ok=False)
    backup = root / "database.dump"
    report = {"id": name, "status": "failed", "target_database": name, "target_removed": False}
    admin_dsn = make_conninfo(dsn, dbname="postgres")
    target_dsn = make_conninfo(dsn, dbname=name)
    created = False
    try:
        with psycopg.connect(dsn) as source:
            source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            snapshot = source.execute("select pg_export_snapshot()").fetchone()[0]
            expected = digest_records(source)
            with backup.open("xb") as output:
                pg_tool(["pg_dump", "--format=custom", "--no-owner", "--no-acl", f"--snapshot={snapshot}"], dsn, stdout=output, container=container)
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
            created = True
        with backup.open("rb") as source:
            pg_tool(["pg_restore", "--exit-on-error", "--no-owner", "--no-acl", "--dbname", name], target_dsn, stdin=source, stdout=subprocess.DEVNULL, container=container)
        with psycopg.connect(target_dsn) as restored:
            actual = digest_records(restored)
        report.update(expected=expected, actual=actual, status="passed" if expected == actual else "failed",
                      elapsed_seconds=round(time.monotonic() - started, 3), backup_bytes=backup.stat().st_size)
        if expected != actual:
            raise RuntimeError("RESTORE_DIGEST_MISMATCH")
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        raise
    finally:
        try:
            if created:
                # Only the UUID name created by this invocation can be dropped.
                with psycopg.connect(admin_dsn, autocommit=True) as admin:
                    admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
                report["target_removed"] = True
        finally:
            (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description="Isolated restore drill; source database is read-only")
    parser.add_argument("--output-dir", default=".data/restore-drills")
    parser.add_argument("--tools-container", default=os.getenv("RESEARCHFORGE_DB_TOOLS_CONTAINER"))
    args = parser.parse_args()
    dsn = os.getenv("RESEARCHFORGE_POSTGRES_DSN")
    if not dsn:
        parser.error("RESEARCHFORGE_POSTGRES_DSN is required")
    report = restore_drill(dsn, args.output_dir, args.tools_container)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
