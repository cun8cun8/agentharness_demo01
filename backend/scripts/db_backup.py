"""Create or restore a PostgreSQL custom-format backup.

The script deliberately invokes pg_dump/pg_restore without a shell so DSNs and
file names cannot be interpreted as command fragments.
"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


def _run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, timeout=3600, check=False)
    if result.returncode:
        raise SystemExit(result.stderr.strip() or f"command failed: {command[0]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="ResearchForge PostgreSQL backup tool")
    parser.add_argument("action", choices=("backup", "restore"))
    parser.add_argument("--dsn", default=os.getenv("RESEARCHFORGE_POSTGRES_DSN"), required=False)
    parser.add_argument("--file", required=True, help="custom-format .dump file")
    parser.add_argument("--confirm-restore", action="store_true", help="acknowledge replacement of existing database objects")
    args = parser.parse_args()
    if not args.dsn:
        parser.error("--dsn or RESEARCHFORGE_POSTGRES_DSN is required")
    if args.action == "restore" and not args.confirm_restore:
        parser.error("restore requires --confirm-restore and an isolated target database")
    target = Path(args.file).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if args.action == "backup":
        _run(["pg_dump", "--format=custom", "--no-owner", "--file", str(target), args.dsn])
    else:
        _run(["pg_restore", "--clean", "--if-exists", "--no-owner", "--dbname", args.dsn, str(target)])
    print(f"{args.action} complete: {target}")


if __name__ == "__main__":
    main()
