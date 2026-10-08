import json

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import Response

from app.api.context import (
    brief_workspace_id,
    filter_by_workspace,
    require_job_access,
    require_research_brief_access,
    workspace_id_for_request,
    current_user,
)
from app.domain.schemas import (
    AttachResearchEvidenceRequest,
    CitationReviewRequest,
    CreateResearchBriefRequest,
    CreateResearchEvidenceRequest,
    NotebookRunResponse,
    ResearchAcceptanceRequest,
    ResearchBriefResponse,
    ResearchPaper,
)
from app.infra.store import store
from app.research.catalog import list_research_benchmark_tasks
from app.research.providers import search_research_papers
from app.services.job_queue import job_queue
from app.research.cycles import ResearchCycleRequest, ReviewExperimentRequest, run_cycles, review_interruption, _locks
from app.agent.autonomous import run_lease
import asyncio

router = APIRouter(tags=["research"])


@router.post("/research/briefs/{brief_id}/cycles", status_code=202)
async def create_research_cycle(brief_id: str, body: ResearchCycleRequest, request: Request, background_tasks: BackgroundTasks):
    brief = require_research_brief_access(request, store.get_research_brief(brief_id))
    if not brief.papers:
        raise HTTPException(422, "RESEARCH_EVIDENCE_REQUIRED")
    job = store.create_job(kind="research_cycle", resource_id=brief.id, task_id=brief.task_id,
                           metadata={"workspace_id": brief.workspace_id, "request": body.model_dump()})
    await job_queue.enqueue(background_tasks, execute_research_cycle, job.id, brief.id, body.model_dump())
    return job


async def execute_research_cycle(job_id, brief_id, data):
    return await run_cycles(store, job_id, brief_id, data)


@router.post("/research/cycles/{job_id}/review-resume", status_code=202)
async def review_cycle(job_id: str, body: ReviewExperimentRequest, request: Request, background_tasks: BackgroundTasks):
    async with _locks.setdefault(job_id, asyncio.Lock()), run_lease("research-" + job_id):
        job = require_job_access(request, store.get_job(job_id))
        try:
            if not (job.status == "paused" and job.metadata.get("resume_ready")):
                review_interruption(store, job, body, current_user(request).id)
            store.update_job(job.id, "queued", metadata={"resume_ready": False})
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        try:
            await job_queue.enqueue(background_tasks, execute_research_cycle, job.id, job.resource_id, job.metadata.get("request", {}))
        except Exception as exc:
            store.update_job(job.id, "paused", "RESEARCH_RESUME_ENQUEUE_FAILED", metadata={"resume_ready": True})
            raise HTTPException(503, "RESEARCH_RESUME_ENQUEUE_FAILED") from exc
    return job


@router.post("/research/briefs", response_model=ResearchBriefResponse)
async def create_research_brief(
    request: CreateResearchBriefRequest,
    request_context: Request,
) -> ResearchBriefResponse:
    request = request.model_copy(
        update={"workspace_id": workspace_id_for_request(request_context, request.workspace_id)}
    )
    try:
        return store.create_research_brief(request)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/research/search")
async def search_research_evidence(request: CreateResearchBriefRequest) -> dict[str, object]:
    papers, source_summary = search_research_papers(request)
    return {"papers": papers, "source_summary": source_summary, "total": len(papers)}


@router.post("/research/evidence", response_model=ResearchPaper)
async def import_research_evidence(
    request: CreateResearchEvidenceRequest,
    request_context: Request,
) -> ResearchPaper:
    workspace_id = workspace_id_for_request(request_context, request.workspace_id)
    request = request.model_copy(update={"workspace_id": workspace_id})
    job = store.create_job(
        kind="research_evidence_import",
        resource_id=request.file_path or request.title or "inline_evidence",
        metadata={
            "domain": request.domain,
            "source_type": request.source_type,
            "file_path": request.file_path,
            "content_char_count": len(request.content or ""),
            "workspace_id": workspace_id,
        },
    )
    store.update_job(job.id, "running")
    try:
        paper = store.create_research_evidence(request, job_id=job.id)
    except ValueError as exc:
        store.update_job(job.id, "failed", str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    store.update_job(
        job.id,
        "completed",
        result_json={
            "paper_id": paper.id,
            "title": paper.title,
            "domain": paper.domain,
            "source": paper.source,
            "snippet_count": len(paper.evidence_snippets),
            **paper.source_metadata,
        },
    )
    return paper


@router.get("/research/evidence")
async def list_research_evidence(
    request: Request,
    domain: str | None = None,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    workspace_id = workspace_id_for_request(request)
    items = store.list_research_evidence(domain=domain, workspace_id=workspace_id)
    return {"items": items[offset : offset + limit], "total": len(items)}


@router.get("/research/evidence/export")
async def export_research_evidence(request: Request, domain: str | None = None) -> Response:
    workspace_id = workspace_id_for_request(request)
    items = store.list_research_evidence(domain=domain, workspace_id=workspace_id)
    lines = [
        {
            "schema_version": "research_evidence_v1",
            "paper": item.model_dump(mode="json"),
        }
        for item in items
    ]
    content = "\n".join(json.dumps(item, ensure_ascii=True) for item in lines)
    if content:
        content += "\n"
    return Response(
        content=content,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=researchforge-evidence.jsonl"},
    )


@router.get("/research/briefs")
async def list_research_briefs(
    request: Request,
    domain: str | None = None,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    workspace_id = workspace_id_for_request(request)
    items = store.list_research_briefs(domain=domain, workspace_id=workspace_id)
    return {"items": items[offset : offset + limit], "total": len(items)}


@router.get("/research/briefs/{brief_id}", response_model=ResearchBriefResponse)
async def get_research_brief(request: Request, brief_id: str) -> ResearchBriefResponse:
    return require_research_brief_access(request, store.get_research_brief(brief_id))


@router.get("/research/briefs/{brief_id}/report")
async def export_research_report(request: Request, brief_id: str) -> Response:
    brief = require_research_brief_access(request, store.get_research_brief(brief_id))
    return Response(
        content=brief.report,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={brief_id}-research-brief.md"},
    )


@router.post("/research/briefs/{brief_id}/evidence", response_model=ResearchBriefResponse)
async def attach_research_evidence(
    request_context: Request,
    brief_id: str,
    request: AttachResearchEvidenceRequest,
) -> ResearchBriefResponse:
    require_research_brief_access(request_context, store.get_research_brief(brief_id))
    brief = store.attach_research_evidence(brief_id, request)
    if brief is None:
        raise HTTPException(status_code=404, detail="Research brief or evidence not found")
    return brief


@router.post("/research/briefs/{brief_id}/citations/review", response_model=ResearchBriefResponse)
async def review_research_citation(
    request_context: Request,
    brief_id: str,
    request: CitationReviewRequest,
) -> ResearchBriefResponse:
    require_research_brief_access(request_context, store.get_research_brief(brief_id))
    brief = store.review_research_citation(brief_id, request)
    if brief is None:
        raise HTTPException(status_code=404, detail="Research brief or citation not found")
    return brief


@router.post("/research/briefs/{brief_id}/notebook-runs")
async def create_notebook_run(
    request: Request,
    brief_id: str,
) -> NotebookRunResponse | dict[str, object]:
    brief = require_research_brief_access(request, store.get_research_brief(brief_id))
    job = store.create_job(
        kind="notebook_run",
        resource_id=brief_id,
        task_id=brief.task_id,
        metadata={"brief_id": brief_id, "workspace_id": brief.workspace_id},
    )
    if job_queue.backend == "redis":
        try:
            await job_queue.enqueue(
                None,
                _execute_notebook_run_job,
                brief_id,
                job.id,
            )
        except Exception as exc:
            failed_job = store.update_job(job.id, "failed", str(exc))
            return {
                "status": "failed",
                "error": str(exc),
                "job_id": job.id,
                "job": failed_job or job,
            }
        return {
            "status": "queued",
            "job_id": job.id,
            "brief_id": brief_id,
        }
    return await _execute_notebook_run_job(brief_id, job.id)


async def _execute_notebook_run_job(
    brief_id: str,
    job_id: str,
) -> NotebookRunResponse | dict[str, object]:
    job = store.jobs.get(job_id)
    if job is None:
        raise ValueError(f"Job not found: {job_id}")
    if job.status in {"cancelled", "completed", "failed", "paused"}:
        return {"status": job.status, "job_id": job.id, "job": job}
    if job.cancel_requested:
        cancelled = store.update_job(job.id, "cancelled", "CANCELLED_BY_OPERATOR")
        return {"status": "cancelled", "job_id": job.id, "job": cancelled or job}
    store.update_job(job.id, "running")
    try:
        run = store.create_notebook_run(brief_id, job_id=job.id)
    except Exception as exc:
        store.update_job(job.id, "failed", str(exc))
        raise
    if run is None:
        store.update_job(job.id, "failed", "Research brief not found")
        raise HTTPException(status_code=404, detail="Research brief not found")
    execution_status = run.metrics.get("execution_status")
    completed = execution_status == "completed"
    store.update_job(
        job.id,
        "completed" if completed else "failed",
        None if completed else str(execution_status or "NOTEBOOK_EXECUTION_FAILED"),
        result_json={
            "notebook_run_id": run.id,
            "brief_id": brief_id,
            "execution_status": execution_status,
            "execution_exit_code": run.metrics.get("execution_exit_code"),
            "sandbox_backend": run.metrics.get("sandbox_backend"),
            "reproducibility_score": run.metrics.get("reproducibility_score"),
            "model_name": run.metrics.get("model_name"),
            "model_fallback_used": run.metrics.get("model_fallback_used"),
        },
    )
    return run


@router.get("/research/notebook-runs")
async def list_notebook_runs(
    request: Request,
    brief_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    workspace_id = workspace_id_for_request(request)
    if brief_id:
        require_research_brief_access(request, store.get_research_brief(brief_id))
    items = store.list_notebook_runs(brief_id=brief_id)
    items = filter_by_workspace(
        request,
        items,
        lambda item: (
            brief_workspace_id(store.get_research_brief(item.brief_id))
            if store.get_research_brief(item.brief_id) is not None
            else None
        ),
    )
    return {"items": items[offset : offset + limit], "total": len(items)}


@router.post("/research/benchmarks")
async def run_research_benchmark(request: Request) -> dict[str, object]:
    workspace_id = workspace_id_for_request(request)
    briefs = store.list_research_briefs(workspace_id=workspace_id)
    scored = [_score_research_brief(brief) for brief in briefs]
    avg_score = round(sum(item["score"] for item in scored) / len(scored), 2) if scored else 0
    return {
        "benchmark_name": "research_brief_v1",
        "items": scored,
        "summary": {"brief_count": len(scored), "avg_score": avg_score},
    }


@router.post("/research/benchmarks/acceptance")
async def run_research_benchmark_acceptance(
    request: ResearchAcceptanceRequest,
    request_context: Request,
) -> dict[str, object]:
    workspace_id = workspace_id_for_request(request_context)
    job = store.create_job(
        kind="research_acceptance",
        resource_id=request.benchmark_name,
        metadata={
            "limit": request.limit,
            "max_papers": request.max_papers,
            "workspace_id": workspace_id,
            "request": {
                **request.model_dump(mode="json"),
                "workspace_id": workspace_id or "workspace_default",
            },
        },
    )
    request_data = {
        **request.model_dump(mode="json"),
        "workspace_id": workspace_id or "workspace_default",
    }
    if job_queue.backend == "redis":
        try:
            await job_queue.enqueue(
                None,
                _execute_research_benchmark_acceptance_job,
                job.id,
                request_data,
            )
        except Exception as exc:
            failed_job = store.update_job(job.id, "failed", str(exc))
            return {
                "status": "failed",
                "error": str(exc),
                "job_id": job.id,
                "job": failed_job or job,
            }
        return {
            "status": "queued",
            "benchmark_name": request.benchmark_name,
            "acceptance": {
                "job_id": job.id,
                "report_path": f"/api/v1/research/benchmarks/acceptance/{job.id}/report",
            },
        }
    return await _execute_research_benchmark_acceptance_job(job.id, request_data)


async def _execute_research_benchmark_acceptance_job(
    job_id: str,
    request_data: dict[str, object],
) -> dict[str, object]:
    request = ResearchAcceptanceRequest.model_validate(request_data)
    workspace_id = str(request_data.get("workspace_id") or "workspace_default")
    job = store.jobs.get(job_id)
    if job is None:
        raise ValueError(f"Job not found: {job_id}")
    if job.status in {"cancelled", "completed", "failed", "paused"}:
        return {"status": job.status, "job_id": job.id, "job": job}
    if job.cancel_requested:
        cancelled = store.update_job(job.id, "cancelled", "CANCELLED_BY_OPERATOR")
        return {"status": "cancelled", "job_id": job.id, "job": cancelled or job}
    store.update_job(job.id, "running")
    catalog = list_research_benchmark_tasks()
    selected = catalog[: request.limit]
    items: list[dict[str, object]] = []
    try:
        for benchmark_task in selected:
            brief = store.create_research_brief(
                CreateResearchBriefRequest(
                    question=benchmark_task.question,
                    domain=benchmark_task.domain,
                    max_papers=request.max_papers,
                    workspace_id=workspace_id,
                )
            )
            scored = _score_research_brief(brief)
            scored.update(
                {
                    "task_id": benchmark_task.id,
                    "domain": benchmark_task.domain,
                    "success": float(scored["score"]) >= 80.0,
                }
            )
            items.append(scored)
    except Exception as exc:
        store.update_job(job.id, "failed", str(exc))
        raise
    passed_count = sum(1 for item in items if item["success"])
    avg_score = round(sum(float(item["score"]) for item in items) / len(items), 2) if items else 0
    result_json = {
        "benchmark_name": request.benchmark_name,
        "job_id": job.id,
        "task_count": len(items),
        "passed_count": passed_count,
        "failed_count": len(items) - passed_count,
        "all_passed": bool(items) and passed_count == len(items),
        "catalog_count": len(catalog),
        "selected_count": len(selected),
        "max_papers": request.max_papers,
        "avg_score": avg_score,
        "report_path": f"/api/v1/research/benchmarks/acceptance/{job.id}/report",
        "items": items,
    }
    store.add_audit_log(
        action="benchmark.acceptance",
        resource_type="research_benchmark",
        resource_id=request.benchmark_name,
        decision="completed",
        actor_id="system",
        detail_json={"job_id": job.id, "summary": result_json},
    )
    store.update_job(job.id, "completed", result_json=result_json)
    return {
        "status": "completed",
        "benchmark_name": request.benchmark_name,
        "acceptance": {
            "job_id": job.id,
            "task_count": len(items),
            "passed_count": passed_count,
            "failed_count": len(items) - passed_count,
            "all_passed": bool(items) and passed_count == len(items),
            "catalog_count": len(catalog),
            "selected_count": len(selected),
            "max_papers": request.max_papers,
            "avg_score": avg_score,
            "report_path": result_json["report_path"],
            "items": items,
        },
    }


job_queue.register_handler(_execute_notebook_run_job)
job_queue.register_handler(_execute_research_benchmark_acceptance_job)


@router.get("/research/benchmarks/acceptance/{job_id}/report")
async def research_acceptance_report(request: Request, job_id: str) -> Response:
    job = require_job_access(request, store.jobs.get(job_id))
    if job.kind != "research_acceptance":
        raise HTTPException(status_code=400, detail="Job is not a research acceptance run")
    content = _build_research_acceptance_report(job.result_json, job.status)
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={job_id}-research-acceptance.md"},
    )


@router.get("/research/benchmarks/validation")
async def validate_research_benchmark_catalog() -> dict[str, object]:
    catalog = list_research_benchmark_tasks()
    ids = [item.id for item in catalog]
    return {
        "benchmark_name": "research_brief_v1",
        "task_count": len(catalog),
        "expected_task_count": 10,
        "all_valid": len(catalog) == 10 and len(set(ids)) == len(ids),
        "items": [
            {
                "id": item.id,
                "domain": item.domain,
                "question": item.question,
            }
            for item in catalog
        ],
    }


def _score_research_brief(brief: ResearchBriefResponse) -> dict[str, object]:
    citation_count = len(brief.citations)
    approved_count = sum(
        1 for item in brief.citations if item.get("status") == "approved"
    )
    citation_coverage = min(1.0, citation_count / max(1, len(brief.papers)))
    review_rate = approved_count / citation_count if citation_count else 0
    score = (
        min(1.0, len(brief.papers) / 5) * 30
        + min(1.0, len(brief.hypotheses) / 2) * 20
        + min(1.0, len(brief.experiments) / 2) * 20
        + citation_coverage * 15
        + review_rate * 15
    )
    return {
        "brief_id": brief.id,
        "question": brief.question,
        "score": round(score, 2),
        "paper_count": len(brief.papers),
        "citation_count": citation_count,
        "approved_citation_count": approved_count,
        "citation_coverage": round(citation_coverage, 4),
        "citation_review_rate": round(review_rate, 4),
    }


def _build_research_acceptance_report(result: dict[str, object], status: str) -> str:
    catalog = list_research_benchmark_tasks()
    ids = [item.id for item in catalog]
    items = [item for item in result.get("items", []) if isinstance(item, dict)]
    lines = [
        "# ResearchForge 研究验收报告",
        "",
        "## 总览",
        "",
        f"- Benchmark：{result.get('benchmark_name', 'research_brief_v1')}",
        f"- 后台任务：{result.get('job_id', '-')}",
        f"- 任务状态：{_research_job_status_label(status)}",
        f"- 验收任务数：{result.get('task_count', 0)}",
        f"- 通过数：{result.get('passed_count', 0)}",
        f"- 失败数：{result.get('failed_count', 0)}",
        f"- 全部通过：{_yes_no(result.get('all_passed', False))}",
        f"- 平均分：{float(result.get('avg_score', 0)):.2f}",
        f"- 每题最大论文数：{result.get('max_papers', '-')}",
        "",
        "## 目录校验",
        "",
        f"- 目录任务数：{len(catalog)}",
        "- 期望任务数：10",
        f"- ID 唯一：{_yes_no(len(set(ids)) == len(ids))}",
        f"- 领域与问题完整：{_yes_no(all(item.domain and item.question for item in catalog))}",
        "",
        "## 评分口径",
        "",
        "- 论文证据覆盖：30 分",
        "- 研究假设完整度：20 分",
        "- 实验设计完整度：20 分",
        "- 引用覆盖率：15 分",
        "- 人工引用审核率：15 分",
        "",
        "## 任务明细",
        "",
        "| 任务 | 领域 | 通过 | 分数 | 论文 | 引用 | 审核率 | 研究问题 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    if items:
        for item in items:
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(item.get("task_id", "-")),
                        str(item.get("domain", "-")),
                        _yes_no(item.get("success", False)),
                        f"{float(item.get('score', 0)):.2f}",
                        str(item.get("paper_count", 0)),
                        str(item.get("citation_count", 0)),
                        f"{float(item.get('citation_review_rate', 0)):.2%}",
                        str(item.get("question", "-")).replace("|", "｜"),
                    ]
                )
                + " |"
            )
    else:
        lines.append("| - | - | 否 | 0.00 | 0 | 0 | 0.00% | 暂无验收结果 |")
    lines.extend(
        [
            "",
            "## 结论",
            "",
            (
                "研究验收已通过，当前 Auto Research P0 能批量生成证据、假设、实验设计和引用链路。"
                if result.get("all_passed")
                else "研究验收未全部通过，请优先复核失败任务的证据数量、引用覆盖和人工审核链路。"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _yes_no(value: object) -> str:
    return "是" if bool(value) else "否"


def _research_job_status_label(value: object) -> str:
    return {
        "queued": "排队中",
        "running": "运行中",
        "completed": "已完成",
        "failed": "失败",
        "cancelled": "已取消",
    }.get(str(value or ""), str(value or "-"))
