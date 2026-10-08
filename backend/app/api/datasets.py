from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from app.api.context import (
    filter_by_workspace,
    is_admin,
    require_preference_pair_access,
    require_run_access,
    require_snapshot_access,
    require_trace_item_access,
    workspace_id_for_request,
)
from app.data_flywheel.exporter import TraceDatasetExporter
from app.domain.schemas import (
    CreateDatasetSnapshotRequest,
    CreatePreferencePairRequest,
    UpdatePreferencePairRequest,
    CreateTraceDatasetItemRequest,
    DatasetSnapshotResponse,
    PreferencePairResponse,
    TraceDatasetItemResponse,
    UpdateTraceDatasetItemRequest,
)
from app.infra.store import store
from app.services.job_queue import job_queue

router = APIRouter(tags=["datasets"])


async def _execute_training_bundle_export_job(
    job_id: str,
    include_pending: bool = False,
    include_rejected: bool = False,
    workspace_id: str | None = None,
) -> None:
    job = store.read_job(job_id)
    if job is None:
        return
    if job.cancel_requested:
        store.update_job(job_id, "cancelled", "CANCELLED_BEFORE_EXPORT")
        return
    store.update_job(job_id, "running")
    try:
        exporter = TraceDatasetExporter(store)
        bundle = exporter.training_bundle(
            include_pending=include_pending,
            include_rejected=include_rejected,
            workspace_id=workspace_id,
        )
        content = exporter.training_bundle_json(
            include_pending=include_pending,
            include_rejected=include_rejected,
            workspace_id=workspace_id,
        )
        if (store.read_job(job_id) or job).cancel_requested:
            store.update_job(job_id, "cancelled", "CANCELLED_AFTER_EXPORT")
            return
        # Job ids are globally unique, so omitting the workspace prefix keeps the
        # download address deterministic without exposing filesystem paths.
        store.artifact_blob_store.save(job_id, content)
    except Exception as exc:
        store.update_job(job_id, "failed", str(exc))
        return
    store.update_job(
        job_id,
        "completed",
        result_json={
            "schema_version": "training_bundle_v1",
            **bundle.get("summary", {}),
            "include_pending": include_pending,
            "include_rejected": include_rejected,
            "artifact_id": job_id,
            "download_path": f"/api/v1/datasets/training-bundle/export/jobs/{job_id}/download",
        },
    )


job_queue.register_handler(_execute_training_bundle_export_job)


def _iter_trace_item_pages(
    *,
    workspace_id: str | None,
    quality_label: str | None = None,
    trace_type: str | None = None,
    use_case: str | None = None,
    failure_type: str | None = None,
    status: str | None = None,
    page_size: int = 100,
):
    """Yield trace items in bounded pages for streaming exports."""
    query_page = getattr(store, "query_trace_dataset_items_page", None)
    if callable(query_page):
        offset = 0
        while True:
            page = query_page(
                workspace_id=workspace_id,
                quality_label=quality_label,
                trace_type=trace_type,
                use_case=use_case,
                failure_type=failure_type,
                status=status,
                limit=page_size,
                offset=offset,
            )
            items = page.get("items", [])
            if not items:
                return
            yield from items
            offset += len(items)
            if offset >= int(page.get("total", 0)):
                return
    items = store.list_trace_dataset_items(
        quality_label=quality_label,
        trace_type=trace_type,
        use_case=use_case,
        failure_type=failure_type,
        status=status,
        workspace_id=workspace_id,
    )
    for offset in range(0, len(items), page_size):
        yield from items[offset : offset + page_size]


@router.post("/datasets/trace-items", response_model=TraceDatasetItemResponse)
async def create_trace_item(
    request: CreateTraceDatasetItemRequest,
    request_context: Request,
) -> TraceDatasetItemResponse:
    require_run_access(request_context, store.get_run(request.agent_run_id))
    try:
        return store.create_trace_dataset_item(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/datasets/trace-items/{item_id}", response_model=TraceDatasetItemResponse)
async def get_trace_item(request: Request, item_id: str) -> TraceDatasetItemResponse:
    return require_trace_item_access(request, store.get_trace_dataset_item(item_id))


@router.get("/datasets/trace-items")
def list_trace_items(
    request: Request,
    query: str | None = Query(default=None, max_length=200),
    quality_label: str | None = None,
    trace_type: str | None = None,
    use_case: str | None = None,
    failure_type: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    workspace_id = workspace_id_for_request(request)
    query_page = getattr(store, "query_trace_dataset_items_page", None)
    if callable(query_page):
        return query_page(
            workspace_id=workspace_id,
            quality_label=quality_label,
            trace_type=trace_type,
            use_case=use_case,
            failure_type=failure_type,
            status=status,
            query=query,
            limit=limit,
            offset=offset,
        )
    items = store.list_trace_dataset_items(
        quality_label=quality_label,
        trace_type=trace_type,
        use_case=use_case,
        failure_type=failure_type,
        status=status,
        workspace_id=workspace_id,
    )
    needle = (query or "").strip().lower()
    if needle:
        items = [
            item
            for item in items
            if needle in " ".join(
                str(value or "")
                for value in (
                    item.id,
                    item.agent_run_id,
                    item.quality_label,
                    item.trace_type,
                    item.use_case,
                    item.failure_type,
                    item.root_cause,
                    item.notes,
                )
            ).lower()
        ]
    return {"items": items[offset : offset + limit], "total": len(items)}


@router.patch("/datasets/trace-items/{item_id}", response_model=TraceDatasetItemResponse)
async def update_trace_item(
    request_context: Request,
    item_id: str,
    request: UpdateTraceDatasetItemRequest,
) -> TraceDatasetItemResponse:
    require_trace_item_access(request_context, store.get_trace_dataset_item(item_id))
    try:
        item = store.update_trace_dataset_item(item_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Trace dataset item not found")
    return item


@router.get("/datasets/export")
async def export_trace_items(
    request: Request,
    quality_label: str | None = None,
    trace_type: str | None = None,
    use_case: str | None = None,
    failure_type: str | None = None,
    status: str | None = None,
) -> Response:
    workspace_id = workspace_id_for_request(request)
    return StreamingResponse(
        TraceDatasetExporter(store).iter_jsonl(
            _iter_trace_item_pages(
                workspace_id=workspace_id,
                quality_label=quality_label,
                trace_type=trace_type,
                use_case=use_case,
                failure_type=failure_type,
                status=status,
            )
        ),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=researchforge-traces.jsonl"},
    )


@router.get("/datasets/quality-report")
async def dataset_quality_report(request: Request) -> dict[str, object]:
    workspace_id = workspace_id_for_request(request)
    report = TraceDatasetExporter(store).quality_report(workspace_id=workspace_id)
    return {"report": report}


@router.get("/datasets/training-bundle/export")
async def export_training_bundle(
    request: Request,
    include_pending: bool = False,
    include_rejected: bool = False,
) -> Response:
    workspace_id = workspace_id_for_request(request)
    job = store.create_job(
        kind="dataset_export",
        resource_id="training_bundle_v1",
        metadata={
            "include_pending": include_pending,
            "include_rejected": include_rejected,
            "workspace_id": workspace_id,
        },
    )
    store.update_job(job.id, "running")
    try:
        exporter = TraceDatasetExporter(store)
        bundle = exporter.training_bundle(
            include_pending=include_pending,
            include_rejected=include_rejected,
            workspace_id=workspace_id,
        )
        content = exporter.training_bundle_json(
            include_pending=include_pending,
            include_rejected=include_rejected,
            workspace_id=workspace_id,
        )
    except Exception as exc:
        store.update_job(job.id, "failed", str(exc))
        raise
    store.update_job(
        job.id,
        "completed",
        result_json={
            "schema_version": "training_bundle_v1",
            **bundle.get("summary", {}),
            "include_pending": include_pending,
            "include_rejected": include_rejected,
        },
    )
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=researchforge-training-bundle.json"},
    )


@router.post("/datasets/training-bundle/export/jobs", response_model=dict[str, object])
async def create_training_bundle_export_job(
    request: Request,
    background_tasks: BackgroundTasks,
    include_pending: bool = False,
    include_rejected: bool = False,
) -> dict[str, object]:
    workspace_id = workspace_id_for_request(request)
    job = store.create_job(
        kind="dataset_export",
        resource_id="training_bundle_v1",
        metadata={
            "include_pending": include_pending,
            "include_rejected": include_rejected,
            "workspace_id": workspace_id,
            "mode": "background",
        },
    )
    await job_queue.enqueue(
        background_tasks,
        _execute_training_bundle_export_job,
        job.id,
        include_pending,
        include_rejected,
        workspace_id,
    )
    return {
        "job": store.read_job(job.id) or job,
        "status_path": f"/api/v1/jobs/{job.id}",
        "download_path": f"/api/v1/datasets/training-bundle/export/jobs/{job.id}/download",
    }


@router.get("/datasets/training-bundle/export/jobs/{job_id}/download")
async def download_training_bundle_export(request: Request, job_id: str) -> Response:
    from app.api.context import require_job_access

    job = require_job_access(request, store.read_job(job_id))
    if job.kind != "dataset_export" or job.resource_id != "training_bundle_v1":
        raise HTTPException(status_code=404, detail="Training bundle export not found")
    if job.status != "completed":
        raise HTTPException(status_code=409, detail="Training bundle export is not completed")
    content = store.artifact_blob_store.load(job_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Training bundle artifact not found")
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=researchforge-training-bundle.json"},
    )


@router.post("/datasets/preference-pairs", response_model=PreferencePairResponse)
async def create_preference_pair(
    request: CreatePreferencePairRequest,
    request_context: Request,
) -> PreferencePairResponse:
    chosen = store.get_run(request.chosen_run_id)
    rejected = store.get_run(request.rejected_run_id)
    if chosen is None or rejected is None:
        raise HTTPException(status_code=404, detail="Both preference runs must exist")
    require_run_access(request_context, chosen)
    require_run_access(request_context, rejected)
    if request.task_id and (chosen.task_id != request.task_id or rejected.task_id != request.task_id):
        raise HTTPException(status_code=400, detail="Preference runs must belong to task_id")
    if chosen.task_id != rejected.task_id:
        raise HTTPException(status_code=400, detail="Preference runs must belong to the same task")
    try:
        return store.create_preference_pair(
            request.model_copy(update={"task_id": request.task_id or chosen.task_id})
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/datasets/preference-pairs/{pair_id}", response_model=PreferencePairResponse)
async def update_preference_pair(
    request_context: Request,
    pair_id: str,
    request: UpdatePreferencePairRequest,
) -> PreferencePairResponse:
    item = require_preference_pair_access(
        request_context,
        store.preference_pairs.get(pair_id),
    )
    item = store.update_preference_pair(pair_id, request)
    if item is None:
        raise HTTPException(status_code=404, detail="Preference pair not found")
    return item


@router.get("/datasets/preference-pairs")
async def list_preference_pairs(
    request: Request,
    query: str | None = Query(default=None, max_length=200),
    task_id: str | None = None,
    use_case: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    workspace_id = workspace_id_for_request(request)
    if task_id:
        from app.api.context import require_task_access

        require_task_access(request, store.get_task(task_id))
    items = store.list_preference_pairs(
        task_id=task_id,
        use_case=use_case,
        workspace_id=workspace_id,
    )
    if status:
        items = [item for item in items if item.status == status]
    needle = (query or "").strip().lower()
    if needle:
        items = [
            item
            for item in items
            if needle in " ".join(
                str(value or "")
                for value in (
                    item.id,
                    item.task_id,
                    item.chosen_run_id,
                    item.rejected_run_id,
                    item.rationale,
                    item.use_case,
                    item.status,
                )
            ).lower()
        ]
    return {"items": items[offset : offset + limit], "total": len(items)}


@router.get("/datasets/preference-pairs/export")
async def export_preference_pairs(
    request: Request,
    task_id: str | None = None,
    use_case: str | None = None,
) -> Response:
    workspace_id = workspace_id_for_request(request)
    if task_id:
        from app.api.context import require_task_access

        require_task_access(request, store.get_task(task_id))
    items = store.list_preference_pairs(
        task_id=task_id,
        use_case=use_case,
        workspace_id=workspace_id,
    )
    content = TraceDatasetExporter(store).preference_pairs_to_jsonl(items)
    return Response(
        content=content,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=researchforge-preferences.jsonl"},
    )


@router.get("/datasets/failure-cases/export")
async def export_failure_cases(
    request: Request,
    quality_label: str | None = None,
    trace_type: str | None = None,
    use_case: str | None = None,
    failure_type: str | None = None,
    status: str | None = None,
    include_unlabeled: bool = True,
) -> Response:
    workspace_id = workspace_id_for_request(request)
    items = store.list_trace_dataset_items(
        quality_label=quality_label,
        trace_type=trace_type,
        use_case=use_case,
        failure_type=failure_type,
        status=status,
        workspace_id=workspace_id,
    )
    content = TraceDatasetExporter(store).failure_cases_to_jsonl(
        items,
        include_unlabeled=include_unlabeled,
        workspace_id=workspace_id,
    )
    return Response(
        content=content,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=researchforge-failure-cases.jsonl"},
    )


@router.post("/datasets/snapshots", response_model=DatasetSnapshotResponse)
async def create_dataset_snapshot(
    request: CreateDatasetSnapshotRequest,
    request_context: Request,
) -> DatasetSnapshotResponse:
    workspace_id = workspace_id_for_request(request_context, request.workspace_id)
    request = request.model_copy(update={"workspace_id": workspace_id})
    return store.create_dataset_snapshot(request)


@router.get("/datasets/snapshots")
async def list_dataset_snapshots(
    request: Request,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    workspace_id = workspace_id_for_request(request)
    items = store.list_dataset_snapshots(status=status, workspace_id=workspace_id)
    return {"items": items[offset : offset + limit], "total": len(items)}


@router.get("/datasets/snapshots/{snapshot_id}/export")
async def export_dataset_snapshot(request: Request, snapshot_id: str) -> Response:
    require_snapshot_access(request, store.get_dataset_snapshot(snapshot_id))
    payload = store.dataset_snapshot_payload(snapshot_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Dataset snapshot not found")
    return Response(
        content=TraceDatasetExporter(store).snapshot_to_json(payload),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={snapshot_id}.json"},
    )
