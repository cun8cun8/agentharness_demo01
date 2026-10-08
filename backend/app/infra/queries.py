SEARCH_FIELDS = {
    "tasks": ("id", "title", "goal", "repo_path"),
    "runs": (
        "id",
        "task_id",
        "task_title",
        "repository_id",
        "project_name",
        "repo_path",
        "model_name",
        "error_summary",
    ),
}


def page_records(store, collection, *, workspace_id=None, filters=None, query=None, limit=50, offset=0):
    if collection not in SEARCH_FIELDS:
        raise ValueError("UNSUPPORTED_QUERY_COLLECTION")
    # Browser query strings commonly contain optional filters as `status=`.
    # Treat blank values as absent instead of filtering for an impossible empty status.
    normalized_filters = {
        key: value
        for key, value in (filters or {}).items()
        if value is not None and (not isinstance(value, str) or value.strip())
    }
    if hasattr(store, "query_records"):
        return store.query_records(collection, workspace_id=workspace_id, filters=normalized_filters, query=query, limit=limit, offset=offset)
    items = [item.model_dump(mode="json") for item in getattr(store, collection).values()]
    if workspace_id:
        task_ids = {item.id for item in store.tasks.values() if item.workspace_id == workspace_id}
        items = [item for item in items if (item.get("workspace_id") == workspace_id if collection == "tasks" else item["task_id"] in task_ids)]
    for key, expected in normalized_filters.items():
        items = [item for item in items if item.get(key) == expected]
    needle = (query or "").strip().lower()
    if needle:
        items = [item for item in items if any(needle in str(item.get(key) or "").lower() for key in SEARCH_FIELDS[collection])]
    items.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
    return {"items": items[offset:offset + limit], "total": len(items)}
