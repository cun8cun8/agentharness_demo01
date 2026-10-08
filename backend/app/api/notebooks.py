import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.api.context import require_research_brief_access
from app.domain.schemas import NotebookRunResponse
from app.infra.idgen import id_generator
from app.infra.store import store
from app.research.notebooks import ExecuteNotebookRequest, execute_notebook
from app.services.job_queue import job_queue

router = APIRouter(tags=["research"])


@router.post("/research/briefs/{brief_id}/execute")
async def execute(request: Request, brief_id: str, body: ExecuteNotebookRequest):
    brief = require_research_brief_access(request, store.get_research_brief(brief_id))
    job = store.create_job(kind="notebook_execute", resource_id=brief_id, task_id=brief.task_id, metadata={"workspace_id": brief.workspace_id, "request": body.model_dump(mode="json")})
    if job_queue.backend == "redis":
        await job_queue.enqueue(None, execute_notebook_job, job.id, brief_id, body.model_dump(mode="json"))
        return {"status": "queued", "job_id": job.id}
    return await execute_notebook_job(job.id, brief_id, body.model_dump(mode="json"))


async def execute_notebook_job(job_id: str, brief_id: str, data: dict):
    job = store.jobs.get(job_id)
    if job is None or job.status in {"completed", "failed", "cancelled"}:
        return {"status": job.status if job else "missing", "job_id": job_id}
    store.update_job(job_id, "running")
    try:
        result = await asyncio.to_thread(execute_notebook, ExecuteNotebookRequest.model_validate(data))
        notebook = NotebookRunResponse(id=id_generator.next("notebook"), brief_id=brief_id, job_id=job_id, **result)
        store.notebook_runs[notebook.id] = notebook
        store.add_audit_log(action="notebook.execute", resource_type="research_brief", resource_id=brief_id, decision=notebook.status, detail_json={"notebook_id": notebook.id, "job_id": job_id})
        store.update_job(job_id, notebook.status, result_json={"notebook_id": notebook.id, "download_path": f"/api/v1/research/notebook-runs/{notebook.id}/download"})
        return notebook
    except Exception as exc:
        store.update_job(job_id, "failed", "NOTEBOOK_EXECUTION_FAILED")
        raise HTTPException(400 if isinstance(exc, ValueError) else 503, str(exc) if isinstance(exc, ValueError) else "NOTEBOOK_EXECUTION_FAILED") from exc


@router.get("/research/notebook-runs/{notebook_id}", response_model=NotebookRunResponse)
def get_notebook(request: Request, notebook_id: str):
    notebook = store.notebook_runs.get(notebook_id)
    if notebook is None:
        raise HTTPException(404, "NOTEBOOK_NOT_FOUND")
    require_research_brief_access(request, store.get_research_brief(notebook.brief_id))
    return notebook


@router.get("/research/notebook-runs/{notebook_id}/download")
def download(request: Request, notebook_id: str):
    notebook = get_notebook(request, notebook_id)
    import nbformat
    cells = []
    for cell in notebook.cells:
        if "cell_type" in cell:
            cells.append(nbformat.from_dict(cell))
        else:
            cells.append(nbformat.v4.new_markdown_cell(str(cell.get("source", ""))))
    payload = nbformat.v4.new_notebook(cells=cells)
    return Response(nbformat.writes(payload), media_type="application/x-ipynb+json", headers={"Content-Disposition": f'attachment; filename="{notebook_id}.ipynb"'})
