from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from app.agent.runtime import AgentRuntime
from app.api.context import (
    audit_log_workspace_id,
    evaluation_workspace_id,
    filter_by_workspace,
    require_evaluation_access,
)
from app.benchmarks.catalog import list_golden_tasks
from app.domain.schemas import (
    CreateEvaluationRunRequest,
    CreateMemoryItemRequest,
    EvaluationRunResponse,
    ReleaseGateRequest,
    ReleaseGateResponse,
    RunStatus,
    StrategyComparisonRequest,
)
from app.evals.benchmark_runner import BenchmarkRunner
from app.infra.idgen import id_generator
from app.infra.store import store
from app.services.job_queue import job_queue

router = APIRouter(tags=["evaluations"])
benchmark_runner = BenchmarkRunner(store=store, runtime=AgentRuntime(store=store))


@router.post("/evaluations/runs")
async def create_evaluation_run(
    request: CreateEvaluationRunRequest,
) -> EvaluationRunResponse | dict[str, object]:
    evaluation_id = id_generator.next("eval")
    job = store.create_job(
        kind="evaluation",
        resource_id=evaluation_id,
        metadata={
            "agent_strategy_id": request.agent_strategy_id,
            "policy_version_id": request.policy_version_id,
            "model_name": request.model_name,
            "task_count": len(request.task_ids),
            "request": request.model_dump(mode="json"),
            "evaluation_id": evaluation_id,
        },
    )
    request_data = request.model_dump(mode="json")
    if job_queue.backend == "redis":
        placeholder = EvaluationRunResponse(
            id=evaluation_id,
            benchmark_name=request.benchmark_name,
            policy_version_id=request.policy_version_id,
            agent_strategy_id=request.agent_strategy_id,
            model_name=request.model_name or "model_pending",
            status=RunStatus.QUEUED,
            job_id=job.id,
            summary={
                "status": "queued",
                "job_id": job.id,
                "task_count": len(request.task_ids),
            },
        )
        store.add_evaluation_run(placeholder)
        try:
            await job_queue.enqueue(
                None,
                _execute_evaluation_job,
                job.id,
                evaluation_id,
                request_data,
            )
        except Exception as exc:
            failed_job = store.update_job(job.id, "failed", str(exc))
            failed = placeholder.model_copy(
                update={
                    "status": RunStatus.FAILED,
                    "summary": {
                        **placeholder.summary,
                        "status": "failed",
                        "error": str(exc),
                    },
                }
            )
            store.update_evaluation_run(failed)
            return {
                "status": "failed",
                "error": str(exc),
                "job_id": job.id,
                "evaluation": failed,
                "job": failed_job or job,
            }
        return {
            "status": "queued",
            "job_id": job.id,
            "evaluation": placeholder,
        }
    return await _execute_evaluation_job(job.id, evaluation_id, request_data)


async def _execute_evaluation_job(
    job_id: str,
    evaluation_id: str,
    request_data: dict[str, object],
) -> EvaluationRunResponse:
    request = CreateEvaluationRunRequest.model_validate(request_data)
    job = store.jobs.get(job_id)
    if job is None:
        raise ValueError(f"Job not found: {job_id}")
    if job.status in {"cancelled", "completed", "failed", "paused"}:
        existing = store.get_evaluation_run(evaluation_id)
        if existing is not None:
            return existing
        raise ValueError(f"Evaluation has no stored result: {evaluation_id}")
    if job.cancel_requested:
        store.update_job(job.id, "cancelled", "CANCELLED_BY_OPERATOR")
        existing = store.get_evaluation_run(evaluation_id)
        if existing is not None:
            cancelled = existing.model_copy(
                update={
                    "status": RunStatus.CANCELLED,
                    "summary": {
                        **existing.summary,
                        "status": "cancelled",
                        "error": "CANCELLED_BY_OPERATOR",
                    },
                }
            )
            store.update_evaluation_run(cancelled)
            return cancelled
        raise ValueError(f"Evaluation was cancelled: {evaluation_id}")
    store.update_job(job.id, "running")
    try:
        evaluation = await benchmark_runner.run(request, evaluation_id=evaluation_id)
    except Exception as exc:
        store.update_job(job.id, "failed", str(exc))
        raise
    evaluation = evaluation.model_copy(update={"job_id": job.id})
    store.update_evaluation_run(evaluation)
    result_json = {
        "evaluation_run_id": evaluation.id,
        "benchmark_name": evaluation.benchmark_name,
        "agent_strategy_id": evaluation.agent_strategy_id,
        "task_count": evaluation.summary.get("task_count", len(evaluation.items)),
        "success_rate": evaluation.summary.get("success_rate", 0),
        "avg_score": evaluation.summary.get("avg_score", 0),
        "regression_count": evaluation.summary.get("regression_count", 0),
    }
    store.add_audit_log(
        action="evaluation.run",
        resource_type="evaluation_run",
        resource_id=evaluation.id,
        decision="completed",
        actor_id="system",
        detail_json={"job_id": job.id, "summary": result_json},
    )
    store.update_job(job.id, "completed", result_json=result_json)
    return evaluation


job_queue.register_handler(_execute_evaluation_job)


@router.post("/evaluations/compare")
async def compare_strategies(request: StrategyComparisonRequest) -> dict[str, object]:
    job = store.create_job(
        kind="strategy_comparison",
        resource_id=request.benchmark_name,
        metadata={
            "agent_strategy_ids": request.agent_strategy_ids,
            "baseline_strategy_id": request.baseline_strategy_id,
            "policy_version_id": request.policy_version_id,
            "model_name": request.model_name,
            "task_count": len(request.task_ids or []),
            "request": request.model_dump(mode="json"),
        },
    )
    if job_queue.backend == "redis":
        try:
            await job_queue.enqueue(
                None,
                _execute_strategy_comparison_job,
                job.id,
                request.model_dump(mode="json"),
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
            "benchmark_name": request.benchmark_name,
        }
    return await _execute_strategy_comparison_job(
        job.id,
        request.model_dump(mode="json"),
    )


async def _execute_strategy_comparison_job(
    job_id: str,
    request_data: dict[str, object],
) -> dict[str, object]:
    request = StrategyComparisonRequest.model_validate(request_data)
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
        comparison = await benchmark_runner.compare_strategies(request)
    except Exception as exc:
        store.update_job(job.id, "failed", str(exc))
        raise
    winner = comparison.get("winner") or {}
    result_json = {
        "benchmark_name": comparison.get("benchmark_name"),
        "task_count": comparison.get("task_count", 0),
        "baseline_strategy_id": comparison.get("baseline_strategy_id"),
        "winner_strategy_id": winner.get("agent_strategy_id"),
        "winner_avg_score": winner.get("avg_score"),
        "strategy_count": len(comparison.get("items", [])),
        "comparison": comparison,
    }
    store.add_audit_log(
        action="evaluation.compare",
        resource_type="benchmark",
        resource_id=request.benchmark_name,
        decision="completed",
        actor_id="system",
        detail_json={"job_id": job.id, "summary": result_json},
    )
    if winner:
        store.create_memory_item(
            CreateMemoryItemRequest(
                scope="project",
                memory_type="strategy",
                key="STRATEGY_COMPARISON_WINNER",
                summary=(
                    f"{winner.get('agent_strategy_id')} 在 {request.benchmark_name} "
                    f"对比中胜出，平均分 {winner.get('avg_score')}，"
                    f"成功率 {winner.get('success_rate')}。"
                ),
                detail_json={
                    "job_id": job.id,
                    "benchmark_name": request.benchmark_name,
                    "baseline_strategy_id": request.baseline_strategy_id,
                    "winner": winner,
                    "items": comparison.get("items", []),
                },
            )
        )
    store.update_job(job.id, "completed", result_json=result_json)
    return comparison


job_queue.register_handler(_execute_strategy_comparison_job)


@router.post("/evaluations/release-gate", response_model=ReleaseGateResponse)
async def release_gate(request: ReleaseGateRequest) -> ReleaseGateResponse:
    job = store.create_job(
        kind="release_gate",
        resource_id=request.evaluation_run_id,
        metadata=request.model_dump(mode="json"),
    )
    store.update_job(job.id, "running")
    evaluation = store.get_evaluation_run(request.evaluation_run_id)
    if evaluation is None:
        store.update_job(job.id, "failed", "Evaluation run not found")
        raise HTTPException(status_code=404, detail="Evaluation run not found")

    try:
        trace_values = [
            float(item.metrics.get("trace_completeness", 0))
            for item in evaluation.items
        ]
        avg_trace_completeness = (
            sum(trace_values) / len(trace_values)
            if trace_values
            else 0
        )
        expected_alignment_values = [
            float(item.metrics.get("expected_alignment", 0))
            for item in evaluation.items
        ]
        avg_expected_alignment = (
            sum(expected_alignment_values) / len(expected_alignment_values)
            if expected_alignment_values
            else 0
        )
        policy_violation_count = sum(
            int(item.metrics.get("policy_violation_count", 0))
            for item in evaluation.items
        )
        success_rate = float(evaluation.summary.get("success_rate", 0))
        avg_score = float(evaluation.summary.get("avg_score", 0))
        regression_count = int(evaluation.summary.get("regression_count", 0))
        avg_cost = float(evaluation.summary.get("avg_cost", 0))
        baseline_cost = _baseline_cost_for(evaluation.agent_strategy_id, evaluation.benchmark_name)
        cost_growth_ratio = (
            max(0.0, (avg_cost - baseline_cost) / baseline_cost)
            if baseline_cost and baseline_cost > 0
            else 0.0
        )
        checks = {
            "success_rate": {
                "actual": success_rate,
                "required": request.min_success_rate,
                "passed": success_rate >= request.min_success_rate,
            },
            "avg_score": {
                "actual": round(avg_score, 2),
                "required": request.min_avg_score,
                "passed": avg_score >= request.min_avg_score,
            },
            "avg_trace_completeness": {
                "actual": round(avg_trace_completeness, 4),
                "required": request.min_trace_completeness,
                "passed": avg_trace_completeness >= request.min_trace_completeness,
            },
            "expected_alignment": {
                "actual": round(avg_expected_alignment, 4),
                "required": request.min_expected_alignment,
                "passed": avg_expected_alignment >= request.min_expected_alignment,
            },
            "policy_violation_count": {
                "actual": policy_violation_count,
                "allowed": request.max_policy_violations,
                "passed": policy_violation_count <= request.max_policy_violations,
            },
            "regression_count": {
                "actual": regression_count,
                "allowed": request.max_regressions,
                "passed": regression_count <= request.max_regressions,
            },
            "cost_growth_ratio": {
                "actual": round(cost_growth_ratio, 4),
                "allowed": request.max_cost_growth_ratio,
                "baseline_avg_cost": baseline_cost,
                "passed": cost_growth_ratio <= request.max_cost_growth_ratio,
            },
        }
        failed_checks = [
            name
            for name, check in checks.items()
            if not check["passed"]
        ]
        passed = not failed_checks
        strategy = store.get_strategy(evaluation.agent_strategy_id)
        previous_strategy_status = strategy.status if strategy is not None else None
        target_stage = request.release_stage
        if target_stage == "canary" and request.canary_percentage <= 0:
            target_stage = "candidate"
        release_status = "blocked"
        auto_promoted = False
        if strategy is not None:
            if passed:
                if target_stage == "active" or request.auto_promote:
                    strategy.status = "active"
                    release_status = "active"
                    auto_promoted = request.auto_promote and target_stage != "active"
                elif target_stage == "canary":
                    strategy.status = "candidate"
                    release_status = "canary"
                else:
                    strategy.status = "candidate"
                    release_status = "candidate"
                store.upsert_strategy(strategy)
            else:
                release_status = "blocked"
        gate = ReleaseGateResponse(
            evaluation_run_id=evaluation.id,
            agent_strategy_id=evaluation.agent_strategy_id,
            passed=passed,
            status=release_status,
            job_id=job.id,
            release_stage=target_stage,
            canary_percentage=request.canary_percentage if target_stage == "canary" else 0,
            auto_promoted=auto_promoted,
            previous_strategy_status=previous_strategy_status,
            checks=checks,
        )
        saved_gate = store.add_release_gate(gate, job_id=job.id)
        if strategy is not None and passed:
            store.add_audit_log(
                action="release_gate.promote" if release_status == "active" else "release_gate.stage",
                resource_type="agent_strategy",
                resource_id=evaluation.agent_strategy_id,
                decision=release_status,
                actor_id="system",
                detail_json={
                    "job_id": job.id,
                    "evaluation_run_id": evaluation.id,
                    "previous_status": previous_strategy_status,
                    "status": strategy.status,
                    "release_stage": target_stage,
                    "canary_percentage": saved_gate.canary_percentage,
                    "auto_promoted": auto_promoted,
                },
            )
        if not passed:
            store.add_audit_log(
                action="release_gate.rollback",
                resource_type="agent_strategy",
                resource_id=evaluation.agent_strategy_id,
                decision="blocked",
                actor_id="system",
                detail_json={
                    "job_id": job.id,
                    "evaluation_run_id": evaluation.id,
                    "previous_status": previous_strategy_status,
                    "failed_checks": failed_checks,
                    "release_stage": target_stage,
                },
            )
        result_json = {
            "evaluation_run_id": evaluation.id,
            "agent_strategy_id": evaluation.agent_strategy_id,
            "release_gate_status": saved_gate.status,
            "passed": saved_gate.passed,
            "release_stage": saved_gate.release_stage,
            "canary_percentage": saved_gate.canary_percentage,
            "auto_promoted": saved_gate.auto_promoted,
            "previous_strategy_status": saved_gate.previous_strategy_status,
            "failed_checks": failed_checks,
            "success_rate": success_rate,
            "avg_score": avg_score,
            "avg_trace_completeness": round(avg_trace_completeness, 4),
            "avg_expected_alignment": round(avg_expected_alignment, 4),
            "regression_count": regression_count,
            "cost_growth_ratio": round(cost_growth_ratio, 4),
        }
        store.create_memory_item(
            CreateMemoryItemRequest(
                scope="project",
                memory_type="strategy",
                key="RELEASE_GATE_PASSED" if saved_gate.passed else "RELEASE_GATE_BLOCKED",
                summary=(
                    f"{evaluation.agent_strategy_id} 发布门禁"
                    f"{'通过' if saved_gate.passed else '阻断'}；"
                    f"成功率 {success_rate:.2%}，平均分 {avg_score:.2f}。"
                ),
                detail_json={
                    "job_id": job.id,
                    "evaluation_run_id": evaluation.id,
                    "benchmark_name": evaluation.benchmark_name,
                    "failed_checks": failed_checks,
                    "checks": checks,
                },
            )
        )
        store.update_job(job.id, "completed", result_json=result_json)
        return saved_gate
    except Exception as exc:
        store.update_job(job.id, "failed", str(exc))
        raise


@router.get("/evaluations/runs/{evaluation_run_id}", response_model=EvaluationRunResponse)
async def get_evaluation_run(
    request: Request,
    evaluation_run_id: str,
) -> EvaluationRunResponse:
    return require_evaluation_access(request, store.read_evaluation_run(evaluation_run_id))


@router.get("/evaluations/runs/{evaluation_run_id}/report")
async def evaluation_report(request: Request, evaluation_run_id: str) -> Response:
    evaluation = require_evaluation_access(
        request,
        store.read_evaluation_run(evaluation_run_id),
    )
    strategy = store.get_strategy(evaluation.agent_strategy_id)
    strategy_config = strategy.runtime_config if strategy is not None else {}
    release_gates = [
        gate
        for gate in store.list_release_gates()
        if gate.evaluation_run_id == evaluation.id
    ]
    catalog = list_golden_tasks(evaluation.benchmark_name)
    catalog_validation = {
        "task_count": len(catalog),
        "all_have_execution_config": all(bool(item.execution_config) for item in catalog),
        "expected_file_count": sum(1 for item in catalog if item.expected_path),
        "success_criteria_count": sum(1 for item in catalog if item.success_criteria),
    }
    lines = [
        "# ResearchForge 评测验收报告",
        "",
        "## 总览",
        "",
        f"- 评测 ID：{evaluation.id}",
        f"- Benchmark：{evaluation.benchmark_name}",
        f"- 策略：{evaluation.agent_strategy_id}",
        f"- 模型：{evaluation.model_name}",
        f"- 成功率：{float(evaluation.summary.get('success_rate', 0)):.2%}",
        f"- 平均分：{float(evaluation.summary.get('avg_score', 0)):.2f}",
        f"- 平均轨迹完整度：{float(evaluation.summary.get('avg_trace_completeness', 0)):.2%}",
        f"- 平均工具调用：{float(evaluation.summary.get('avg_tool_call_count', 0)):.2f}",
        f"- 平均成本：${float(evaluation.summary.get('avg_cost', 0)):.4f}",
        f"- 回归数：{int(evaluation.summary.get('regression_count', 0))}",
        f"- 基线评测：{evaluation.summary.get('baseline_evaluation_id') or '无'}",
        f"- 失败类型分布：{evaluation.summary.get('failure_type_distribution', {})}",
        "",
        "## Golden Task 目录校验",
        "",
        f"- 目录任务数：{catalog_validation['task_count']}",
        f"- 执行配置完整：{_yes_no(catalog_validation['all_have_execution_config'])}",
        f"- expected.md 数量：{catalog_validation['expected_file_count']}",
        f"- success_criteria 数量：{catalog_validation['success_criteria_count']}",
        "",
        "## 策略运行约束",
        "",
        f"- 预检查：{_yes_no(strategy_config.get('precheck', False))}",
        f"- 验证重试：{_yes_no(strategy_config.get('retry', False))}",
        f"- Critic：{_yes_no(strategy_config.get('critic', False))}",
        f"- 模型网关：{_yes_no(strategy_config.get('model_gateway', False))}",
        f"- 最大验证重试：{strategy_config.get('max_validation_retries', 0)}",
        f"- 最大修改文件数：{strategy_config.get('max_patch_files', 3)}",
        f"- 最大修改行数：{strategy_config.get('max_changed_lines', 80)}",
        f"- 是否允许修改测试：{_yes_no(strategy_config.get('allow_test_edits', False))}",
        "",
        "## 发布门禁记录",
        "",
    ]
    if release_gates:
        lines.extend([
            "| 状态 | 阶段 | 灰度比例 | 通过 | 失败检查 |",
            "| --- | --- | ---: | --- | --- |",
        ])
        for gate in release_gates:
            failed_checks = [
                _gate_check_label(name)
                for name, check in gate.checks.items()
                if isinstance(check, dict) and not check.get("passed", False)
            ]
            lines.append(
                "| "
                + " | ".join(
                    [
                        gate.status,
                        gate.release_stage,
                        f"{gate.canary_percentage}%",
                        _yes_no(gate.passed),
                        "、".join(failed_checks) or "-",
                    ]
                )
                + " |"
            )
    else:
        lines.append("暂无发布门禁记录。")
    lines.extend([
        "",
        "## 任务级验收",
        "",
        "| 任务 | 成功 | 分数 | 测试 | 期望对齐 | 轨迹完整度 | Diff 风险 | 报告质量 | 工具效率 | 成本效率 | 策略阈值 | 失败原因 |",
        "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ])
    for item in evaluation.items:
        tests = (
            f"{item.tests_passed}/{item.tests_total}"
            if item.tests_passed is not None and item.tests_total is not None
            else "-"
        )
        strategy_limits = (
            f"重试 {item.metrics.get('strategy_max_validation_retries', '-')}; "
            f"文件<={item.metrics.get('strategy_max_patch_files', '-')}; "
            f"行数<={item.metrics.get('strategy_max_changed_lines', '-')}"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    item.task_id,
                    "是" if item.success else "否",
                    f"{item.score:.2f}",
                    tests,
                    f"{float(item.metrics.get('expected_alignment', 0)):.2%}",
                    f"{float(item.metrics.get('trace_completeness', 0)):.2%}",
                    f"{float(item.metrics.get('diff_risk_score', 0)):.2f}",
                    f"{float(item.metrics.get('report_quality', 0)):.2f}",
                    f"{float(item.metrics.get('tool_efficiency', 0)):.2f}",
                    f"{float(item.metrics.get('cost_efficiency', 0)):.2f}",
                    strategy_limits,
                    item.failure_reason or "-",
                ]
            )
            + " |"
        )
    lines.extend([
        "",
        "## expected.md 检查明细",
        "",
        "| 任务 | 来源 | 检查项 | 结果 |",
        "| --- | --- | --- | --- |",
    ])
    for item in evaluation.items:
        expected_checks = item.metrics.get("expected_checks") or []
        if not expected_checks:
            lines.append(f"| {item.task_id} | - | 无扩展期望检查 | - |")
            continue
        for check in expected_checks:
            if not isinstance(check, dict):
                continue
            lines.append(
                "| "
                + " | ".join(
                    [
                        item.task_id,
                        str(check.get("source") or "-"),
                        str(check.get("label") or check.get("criterion") or "-"),
                        "通过" if check.get("passed") else "未通过",
                    ]
                )
                + " |"
            )
    content = "\n".join(lines) + "\n"
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={evaluation.id}-report.md"},
    )


def _yes_no(value: object) -> str:
    return "是" if bool(value) else "否"


def _gate_check_label(value: str) -> str:
    return {
        "success_rate": "成功率",
        "avg_score": "平均分",
        "avg_trace_completeness": "轨迹完整度",
        "expected_alignment": "期望对齐率",
        "policy_violation_count": "策略违规数",
        "regression_count": "回归数",
        "cost_growth_ratio": "成本增长率",
    }.get(value, value)


@router.get("/evaluations/runs")
async def list_evaluation_runs(
    request: Request,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    all_items = filter_by_workspace(
        request,
        store.read_evaluation_runs(),
        evaluation_workspace_id,
    )
    return {"items": all_items[offset : offset + limit], "total": len(all_items)}


@router.get("/evaluations/release-gates")
async def list_release_gates(
    request: Request,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    all_items = filter_by_workspace(
        request,
        store.list_release_gates(),
        lambda item: (
            evaluation_workspace_id(store.get_evaluation_run(item.evaluation_run_id))
            if store.get_evaluation_run(item.evaluation_run_id) is not None
            else None
        ),
    )
    return {"items": all_items[offset : offset + limit], "total": len(all_items)}


@router.get("/evaluations/release-gates/strategies/{strategy_id}")
async def list_strategy_release_history(
    request: Request,
    strategy_id: str,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    all_gates = [
        item
        for item in store.list_release_gates()
        if item.agent_strategy_id == strategy_id
    ]
    audit_logs = store.list_audit_logs(resource_type="agent_strategy", resource_id=strategy_id)
    release_events = [
        item
        for item in audit_logs
        if item.action in {"release_gate.evaluate", "release_gate.stage", "release_gate.promote", "release_gate.rollback", "strategy.rollback"}
    ]
    all_gates = filter_by_workspace(
        request,
        all_gates,
        lambda item: (
            evaluation_workspace_id(store.get_evaluation_run(item.evaluation_run_id))
            if store.get_evaluation_run(item.evaluation_run_id) is not None
            else None
        ),
    )
    release_events = filter_by_workspace(request, release_events, audit_log_workspace_id)
    return {
        "strategy_id": strategy_id,
        "items": all_gates[offset : offset + limit],
        "events": release_events[offset : offset + limit],
        "total": len(all_gates),
    }


def _baseline_cost_for(strategy_id: str, benchmark_name: str) -> float | None:
    baseline_strategy_id = "repair_baseline_v1"
    if strategy_id == baseline_strategy_id:
        return None
    candidates = [
        item
        for item in store.evaluation_runs.values()
        if item.benchmark_name == benchmark_name and item.agent_strategy_id == baseline_strategy_id
    ]
    if not candidates:
        return None
    latest = max(candidates, key=lambda item: item.created_at)
    return float(latest.summary.get("avg_cost", 0))


@router.get("/evaluations/runs/{evaluation_id}/quality-comparison")
async def compare_evaluation_quality(request: Request, evaluation_id: str, baseline_id: str):
    from app.services.operations import compare_quality_reports
    current = require_evaluation_access(request, store.get_evaluation_run(evaluation_id))
    baseline = require_evaluation_access(request, store.get_evaluation_run(baseline_id))
    return compare_quality_reports(baseline, current)
