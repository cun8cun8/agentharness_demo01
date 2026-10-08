from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class StoreRecord(Base):
    __tablename__ = "store_records"

    collection: Mapped[str] = mapped_column(String, primary_key=True)
    entity_id: Mapped[str] = mapped_column(String, primary_key=True)
    body: Mapped[Any] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class RepositoryConnectionRecord(Base):
    """Typed canonical storage for repository credentials and GitHub routing."""

    __tablename__ = "repository_connections"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_branch: Mapped[str] = mapped_column(String, nullable=False, default="main")
    credential_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    github_installation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    github_owner: Mapped[str | None] = mapped_column(String, nullable=True)
    github_repository: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class StoreConflictError(RuntimeError):
    """A concurrent writer changed the same field; callers must reload."""


_MISSING = object()


def merge_record(base: Any, local: Any, remote: Any, path: str = "") -> Any:
    if local == base:
        return deepcopy(remote)
    if remote == base or remote == local:
        return deepcopy(local)
    if all(isinstance(item, dict) for item in (base, local, remote)):
        merged = deepcopy(remote)
        for key in sorted(base.keys() | local.keys()):
            old, new = base.get(key, _MISSING), local.get(key, _MISSING)
            current = remote.get(key, _MISSING)
            if old == new:
                continue
            if current != old and current != new:
                if all(isinstance(item, dict) for item in (old, new, current)):
                    merged[key] = merge_record(old, new, current, f"{path}/{key}")
                    continue
                raise StoreConflictError(f"STORE_WRITE_CONFLICT:{path}/{key}")
            if new is _MISSING:
                merged.pop(key, None)
            else:
                merged[key] = deepcopy(new)
        return merged
    raise StoreConflictError(f"STORE_WRITE_CONFLICT:{path}")


def flatten_snapshot(snapshot: dict) -> dict[tuple[str, str], Any]:
    records = {}
    for collection, value in snapshot.items():
        if collection == "events":
            for events in value.values():
                for event in events:
                    records[(collection, event["id"])] = event
        elif collection in {"cancel_requests", "pause_requests"}:
            records.update({(collection, key): True for key in value})
        elif collection == "release_gates":
            # Release gates are response records rather than generic resources, so
            # they intentionally do not expose an ``id``. The release-gate job is
            # unique and provides a stable storage identity; the fallback keeps
            # snapshots created before jobs were persisted readable.
            for position, item in enumerate(value):
                identity = str(
                    item.get("id")
                    or item.get("job_id")
                    or f"{item.get('evaluation_run_id', 'release-gate')}:{item.get('created_at', '')}:{position}"
                )
                records[(collection, identity)] = item
        elif isinstance(value, dict):
            records.update({(collection, key): item for key, item in value.items()})
    return records


def inflate_records(records: dict[tuple[str, str], Any]) -> dict:
    snapshot: dict = {"events": {}, "release_gates": [], "cancel_requests": [], "pause_requests": []}
    for (collection, key), value in records.items():
        if collection == "events":
            snapshot[collection].setdefault(value["run_id"], []).append(value)
        elif collection in {"cancel_requests", "pause_requests"}:
            snapshot[collection].append(key)
        elif collection == "release_gates":
            snapshot[collection].append(value)
        else:
            snapshot.setdefault(collection, {})[key] = value
    for events in snapshot["events"].values():
        events.sort(key=lambda item: (item["timestamp"], item["id"]))
    return snapshot
