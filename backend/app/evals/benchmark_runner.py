import re
from datetime import datetime, timezone

from app.agent.runtime import AgentRuntime
from app.benchmarks.catalog import GoldenTask, find_golden_task_for_repo, list_golden_tasks
from app.domain.schemas import (
    CreateEvaluationRunRequest,
    EvaluationRunResponse,
    EvaluationScore,
    ArtifactType,
    RunStatus,
    StrategyComparisonRequest,
)
from app.infra.idgen import id_generator
from app.infra.store import InMemoryStore


class BenchmarkRunner:
    def __init__(self, store: InMemoryStore, runtime: AgentRuntime) -> None:
        self.store = store
        self.runtime = runtime

    async def run(
        self,
        request: CreateEvaluationRunRequest,
        evaluation_id: str | None = None,
    ) -> EvaluationRunResponse:
        started = datetime.now(timezone.utc)
        items: list[EvaluationScore] = []
        for task_id in request.task_ids:
            task = self.store.get_task(task_id)
            if task is None:
                items.append(
                    EvaluationScore(
                        task_id=task_id,
                        success=False,
                        score=0,
                        failure_reason="TASK_NOT_FOUND",
                    )
                )
                continue

            golden = find_golden_task_for_repo(task.repo_path)
            run = await self.runtime.run_task(
                task_id=task.id,
                agent_strategy_id=request.agent_strategy_id,
                policy_version_id=request.policy_version_id,
                model_name=request.model_name,
            )
            criteria = self._criteria_result(run.id, golden)
            success = self._run_matches_expectation(run.status, criteria)
            score = self._score_run(run.status, run.metrics, criteria)
            items.append(
                EvaluationScore(
                    task_id=task.id,
                    agent_run_id=run.id,
                    success=success,
                    score=score,
                    tests_passed=run.metrics.get("tests_passed"),
                    tests_total=run.metrics.get("tests_total"),
                    cost=run.total_cost,
                    duration_ms=run.duration_ms,
                    failure_reason=None if success else run.error_summary or "RUN_FAILED",
                    metrics={
                        "trace_completeness": run.metrics.get("trace_completeness", 0),
                        "policy_violation_count": run.metrics.get("policy_violation_count", 0),
                        "tool_call_count": run.tool_call_count,
                        "tests_passed_ratio": run.metrics.get("tests_passed_ratio", 0),
                        "expected_alignment": criteria["ratio"],
                        "criteria_met": len(criteria["met"]),
                        "criteria_total": len(criteria["criteria"]),
                        "success_criteria": criteria["criteria"],
                        "met_criteria": criteria["met"],
                        "failed_criteria": criteria["failed"],
                        "expected_checks": criteria["details"],
                        "diff_risk_score": run.metrics.get("diff_risk_score", 0),
                        "report_quality": run.metrics.get("report_quality", 0),
                        "tool_efficiency": run.metrics.get("tool_efficiency", 0),
                        "cost_efficiency": run.metrics.get("cost_efficiency", 0),
                        "critic_review_score": run.metrics.get("critic_review_score", 0),
                        "precheck_score": run.metrics.get("precheck_score", 0),
                        "strategy_precheck_enabled": run.metrics.get("strategy_precheck_enabled", False),
                        "strategy_retry_enabled": run.metrics.get("strategy_retry_enabled", False),
                        "strategy_critic_enabled": run.metrics.get("strategy_critic_enabled", False),
                        "strategy_max_validation_retries": run.metrics.get("strategy_max_validation_retries", 0),
                        "strategy_max_patch_files": run.metrics.get("strategy_max_patch_files", 0),
                        "strategy_max_changed_lines": run.metrics.get("strategy_max_changed_lines", 0),
                        "strategy_allow_test_edits": run.metrics.get("strategy_allow_test_edits", False),
                        "strategy_require_diff": run.metrics.get("strategy_require_diff", True),
                        "strategy_require_all_tests": run.metrics.get("strategy_require_all_tests", True),
                    },
                )
            )

        success_count = sum(1 for item in items if item.success)
        summary = {
            "success_rate": round(success_count / len(items), 4) if items else 0,
            "avg_score": round(sum(item.score for item in items) / len(items), 2) if items else 0,
            "avg_duration_ms": round(sum(item.duration_ms for item in items) / len(items), 2) if items else 0,
            "avg_cost": round(sum(item.cost for item in items) / len(items), 4) if items else 0,
            "avg_tool_call_count": round(
                sum(float(item.metrics.get("tool_call_count", 0)) for item in items) / len(items),
                2,
            ) if items else 0,
            "avg_trace_completeness": round(
                sum(float(item.metrics.get("trace_completeness", 0)) for item in items) / len(items),
                4,
            ) if items else 0,
            "failure_type_distribution": self._failure_distribution(items),
            "regression_count": 0,
            "task_count": len(items),
        }
        resolved_model_name = request.model_name
        if resolved_model_name is None:
            resolved_model_name = "mock-coding-agent"
            for item in items:
                if item.agent_run_id:
                    resolved_run = self.store.get_run(item.agent_run_id)
                    if resolved_run is not None:
                        resolved_model_name = resolved_run.model_name
                        break
        evaluation = EvaluationRunResponse(
            id=evaluation_id or id_generator.next("eval"),
            benchmark_name=request.benchmark_name,
            policy_version_id=request.policy_version_id,
            agent_strategy_id=request.agent_strategy_id,
            model_name=resolved_model_name,
            status=RunStatus.COMPLETED,
            summary=summary,
            items=items,
            started_at=started,
            finished_at=datetime.now(timezone.utc),
        )
        saved = self.store.add_evaluation_run(evaluation)
        return self._attach_baseline_metrics(saved)

    async def compare_strategies(self, request: StrategyComparisonRequest) -> dict:
        task_ids = request.task_ids or self._seed_comparison_tasks()
        evaluations: list[EvaluationRunResponse] = []
        for strategy_id in request.agent_strategy_ids:
            evaluation = await self.run(
                CreateEvaluationRunRequest(
                    benchmark_name=request.benchmark_name,
                    task_ids=task_ids,
                    agent_strategy_id=strategy_id,
                    policy_version_id=request.policy_version_id,
                    model_name=request.model_name,
                )
            )
            evaluations.append(evaluation)

        baseline = next(
            (evaluation for evaluation in evaluations if evaluation.agent_strategy_id == request.baseline_strategy_id),
            evaluations[0] if evaluations else None,
        )
        baseline_scores = {
            item.task_id: item.score
            for item in baseline.items
        } if baseline else {}

        rows = []
        for evaluation in evaluations:
            policy_violations = sum(
                int(item.metrics.get("policy_violation_count", 0))
                for item in evaluation.items
            )
            trace_values = [
                float(item.metrics.get("trace_completeness", 0))
                for item in evaluation.items
            ]
            regressions = sum(
                1
                for item in evaluation.items
                if item.task_id in baseline_scores and item.score + 0.01 < baseline_scores[item.task_id]
            )
            deltas = [
                round(item.score - baseline_scores[item.task_id], 2)
                for item in evaluation.items
                if item.task_id in baseline_scores
            ]
            evaluation.summary.update(
                {
                    "regression_count": regressions,
                    "baseline_evaluation_id": baseline.id if baseline else None,
                    "baseline_avg_score": baseline.summary.get("avg_score", 0) if baseline else None,
                    "score_delta_vs_baseline": round(
                        evaluation.summary.get("avg_score", 0) - baseline.summary.get("avg_score", 0),
                        2,
                    ) if baseline else None,
                }
            )
            self.store.update_evaluation_run(evaluation)
            rows.append(
                {
                    "agent_strategy_id": evaluation.agent_strategy_id,
                    "evaluation_run_id": evaluation.id,
                    "success_rate": evaluation.summary.get("success_rate", 0),
                    "avg_score": evaluation.summary.get("avg_score", 0),
                    "avg_duration_ms": evaluation.summary.get("avg_duration_ms", 0),
                    "avg_cost": evaluation.summary.get("avg_cost", 0),
                    "avg_tool_call_count": evaluation.summary.get("avg_tool_call_count", 0),
                    "avg_trace_completeness": round(sum(trace_values) / len(trace_values), 4) if trace_values else 0,
                    "policy_violation_count": policy_violations,
                    "failure_type_distribution": evaluation.summary.get("failure_type_distribution", {}),
                    "regression_count": regressions,
                    "status": "baseline" if evaluation is baseline else "compared",
                }
            )

        winner = max(rows, key=lambda row: (row["avg_score"], row["success_rate"]), default=None)
        return {
            "benchmark_name": request.benchmark_name,
            "baseline_strategy_id": request.baseline_strategy_id,
            "task_count": len(task_ids),
            "items": rows,
            "winner": winner,
        }

    def _attach_baseline_metrics(
        self,
        evaluation: EvaluationRunResponse,
    ) -> EvaluationRunResponse:
        """Persist standalone-evaluation regression data against the latest baseline."""
        if evaluation.agent_strategy_id == "repair_baseline_v1":
            evaluation.summary.update(
                {
                    "baseline_evaluation_id": None,
                    "baseline_avg_score": None,
                    "score_delta_vs_baseline": 0.0,
                    "regression_count": 0,
                }
            )
            return self.store.update_evaluation_run(evaluation)

        candidates = [
            item
            for item in self.store.list_evaluation_runs()
            if item.id != evaluation.id
            and item.benchmark_name == evaluation.benchmark_name
            and item.agent_strategy_id == "repair_baseline_v1"
            and item.status == RunStatus.COMPLETED
        ]
        baseline = max(candidates, key=lambda item: item.created_at, default=None)
        if baseline is None:
            return evaluation

        baseline_scores = {item.task_id: item.score for item in baseline.items}
        regressions = sum(
            1
            for item in evaluation.items
            if item.task_id in baseline_scores and item.score + 0.01 < baseline_scores[item.task_id]
        )
        evaluation.summary.update(
            {
                "baseline_evaluation_id": baseline.id,
                "baseline_avg_score": baseline.summary.get("avg_score", 0),
                "score_delta_vs_baseline": round(
                    evaluation.summary.get("avg_score", 0) - baseline.summary.get("avg_score", 0),
                    2,
                ),
                "regression_count": regressions,
            }
        )
        return self.store.update_evaluation_run(evaluation)

    def _criteria_result(self, run_id: str, golden: GoldenTask | None) -> dict:
        criteria = list(dict.fromkeys((golden.success_criteria if golden else [
            "all_tests_pass",
            "no_policy_violation",
            "patch_applies_cleanly",
        ]) + self._expected_criteria(golden.expected_text if golden else "")))
        run = self.store.get_run(run_id)
        if run is None:
            return {"criteria": criteria, "met": [], "failed": criteria, "details": [], "ratio": 0.0}
        artifacts = self.store.list_artifacts(run_id)
        diff = next((item for item in artifacts if item.type == ArtifactType.DIFF), None)
        reports = [item for item in artifacts if item.type == ArtifactType.REPORT]
        diff_text = diff.content if diff else ""
        source_path = ""
        if golden is not None:
            source_path = str(golden.execution_config.get("source_path") or "")
        changed_paths = self._changed_paths(diff_text)
        source_diff = self._path_diff_text(diff_text, source_path)
        source_added = "\n".join(self._added_lines(diff_text, source_path))
        added_text = "\n".join(self._added_lines(diff_text))
        source_added_normalized = self._normalize_code(source_added)
        diff_normalized = self._normalize_code(source_diff or diff_text)
        touched_tests = any(
            marker in diff_text
            for marker in [" b/tests/", " a/tests/", "+++ b/test_", "--- a/test_"]
        )
        met: list[str] = []
        details: list[dict[str, object]] = []
        yaml_criteria = set(golden.success_criteria if golden else [])
        for criterion in criteria:
            passed = False
            if criterion == "all_tests_pass":
                if run.status == RunStatus.COMPLETED and run.metrics.get("tests_passed_ratio", 0) == 1:
                    passed = True
            elif criterion == "no_policy_violation":
                if int(run.metrics.get("policy_violation_count", 0)) == 0:
                    passed = True
            elif criterion == "patch_applies_cleanly":
                if diff is not None and run.error_summary != "PATCH_APPLY_FAILED" and " b/tests/" not in diff_text:
                    passed = True
            elif criterion == "failure_report_generated":
                if any(item.name == "failure-report.md" for item in reports):
                    passed = True
            elif criterion == "trace_recorded":
                if self.store.list_steps(run_id) and run.metrics.get("trace_completeness", 0) > 0:
                    passed = True
            elif criterion == "no_test_changes":
                if not touched_tests:
                    passed = True
            elif criterion == "expected_failure_reason_tests_still_failing":
                if run.error_summary == "TESTS_STILL_FAILING":
                    passed = True
            elif criterion == "report_generated":
                if reports:
                    passed = True
            elif criterion == "expected_source_file_modified":
                if source_path and source_path in changed_paths:
                    passed = True
            elif criterion == "no_hardcoded_test_values":
                if not self._contains_hardcoded_test_values(added_text):
                    passed = True
            elif criterion == "no_assertion_relaxation":
                if not touched_tests and not self._removed_assertions_from_tests(diff_text):
                    passed = True
            elif criterion == "no_string_money_return":
                if not re.search(r"^\+\s*return\s+(?:str\(|['\"])", diff_text, flags=re.MULTILINE):
                    passed = True
            elif criterion == "date_empty_or_invalid_returns_none":
                if "return none" in source_added_normalized and ("except valueerror" in diff_normalized or "try:" in diff_normalized):
                    passed = True
            elif criterion == "date_valid_iso_preserved":
                if "fromisoformat" in diff_normalized:
                    passed = True
            elif criterion == "decimal_safe_arithmetic":
                if self._decimal_safe_arithmetic(source_added_normalized):
                    passed = True
            elif criterion == "money_rounded_to_cents":
                if "quantize" in source_added_normalized and ("0.01" in source_added_normalized or "round(" in source_added_normalized):
                    passed = True
            elif criterion == "pagination_ceiling_division":
                if "ceil(" in source_added_normalized or "+ page_size - 1" in source_added_normalized:
                    passed = True
            elif criterion == "pagination_minimum_one":
                if self._pagination_minimum_one(source_added_normalized):
                    passed = True
            elif criterion == "email_strips_whitespace":
                if ".strip()" in source_added_normalized and ".lower()" in source_added_normalized:
                    passed = True
            elif criterion == "email_blank_input_empty":
                if run.metrics.get("tests_passed_ratio", 0) == 1 and ".strip()" in source_added_normalized:
                    passed = True
            elif criterion == "slug_removes_punctuation":
                if "re.sub" in source_added_normalized or "translate(" in source_added_normalized:
                    passed = True
            elif criterion == "slug_collapses_separators":
                if self._slug_collapses_separators(source_added_normalized):
                    passed = True
            elif criterion == "slug_trims_outer_separators":
                if ".strip(\"-\")" in source_added_normalized or ".strip('-')" in source_added_normalized:
                    passed = True
            elif criterion == "inventory_inclusive_threshold":
                if "<=" in source_added_normalized:
                    passed = True
            elif criterion == "csv_skips_header":
                if "- 1" in source_added_normalized or "[1:]" in source_added_normalized or "next(" in source_added_normalized:
                    passed = True
            elif criterion == "csv_ignores_blank_lines":
                if "any(row)" in source_added_normalized or ".strip()" in source_added_normalized:
                    passed = True
            elif criterion == "timezone_preserves_sign":
                if "sign" in source_added_normalized and "+" in source_added_normalized and "-" in source_added_normalized:
                    passed = True
            elif criterion == "timezone_uses_absolute_minutes":
                if "abs(" in source_added_normalized:
                    passed = True
            elif criterion == "timezone_zero_pads":
                if ":02d" in source_added_normalized:
                    passed = True
            elif criterion == "retry_first_attempt_base_delay":
                if "attempt - 1" in source_added_normalized or "attempt-1" in source_added_normalized:
                    passed = True
            elif criterion == "retry_caps_backoff":
                if "min(cap" in source_added_normalized:
                    passed = True
            if passed:
                met.append(criterion)
            details.append(
                {
                    "criterion": criterion,
                    "label": self._criterion_label(criterion),
                    "passed": passed,
                    "source": "task.yaml" if criterion in yaml_criteria else "expected.md",
                }
            )
        failed = [criterion for criterion in criteria if criterion not in met]
        ratio = round(len(met) / len(criteria), 4) if criteria else 0.0
        return {"criteria": criteria, "met": met, "failed": failed, "details": details, "ratio": ratio}

    @staticmethod
    def _run_matches_expectation(status: RunStatus, criteria: dict) -> bool:
        if "failure_report_generated" in criteria["criteria"]:
            return criteria["ratio"] >= 1.0
        return status == RunStatus.COMPLETED and criteria["ratio"] >= 1.0

    @staticmethod
    def _score_run(status: RunStatus, metrics: dict, criteria: dict) -> float:
        if status != RunStatus.COMPLETED and "failure_report_generated" not in criteria["criteria"]:
            return 0
        raw_score = (
            35 * criteria["ratio"]
            + 15 * metrics.get("trace_completeness", 0)
            + 15 * criteria["ratio"]
            + 10 * metrics.get("diff_risk_score", 0)
            + 10 * metrics.get("report_quality", 0)
            + 5 * metrics.get("tool_efficiency", 0)
            + 5 * metrics.get("cost_efficiency", 0)
            + 5 * metrics.get("critic_review_score", 0)
        )
        penalty = 20 * int(metrics.get("policy_violation_count", 0))
        return round(max(0, raw_score - penalty), 2)

    @staticmethod
    def _failure_distribution(items: list[EvaluationScore]) -> dict[str, int]:
        distribution: dict[str, int] = {}
        for item in items:
            if item.success:
                continue
            reason = item.failure_reason or "UNKNOWN_ERROR"
            distribution[reason] = distribution.get(reason, 0) + 1
        return distribution

    def _seed_comparison_tasks(self) -> list[str]:
        task_ids: list[str] = []
        for golden in list_golden_tasks():
            existing = next(
                (
                    task
                    for task in self.store.tasks.values()
                    if task.repo_path == golden.repo_path and task.title == golden.title
                ),
                None,
            )
            task = self.store.create_task(golden.to_task_request()) if existing is None else self.store.update_task(golden.sync_task(existing))
            task_ids.append(task.id)
        return task_ids

    @staticmethod
    def _expected_criteria(expected_text: str) -> list[str]:
        if not expected_text:
            return []
        text = expected_text.lower()
        criteria: list[str] = []
        if "expected fix" in text or "expected behavior" in text or "root cause" in text:
            criteria.append("expected_source_file_modified")
        if any(marker in text for marker in ("do not modify tests", "do not change tests", "不修改测试", "不要修改测试")):
            criteria.append("no_test_changes")
        if any(marker in text for marker in ("relax assertions", "test expectations", "assertions")):
            criteria.append("no_assertion_relaxation")
        if any(marker in text for marker in ("hard-code", "hardcode", "hard coded", "硬编码")):
            criteria.append("no_hardcoded_test_values")
        if any(marker in text for marker in ("failure report", "generate a failure report", "失败报告", "生成失败报告")):
            criteria.append("failure_report_generated")
        if any(marker in text for marker in ("trace", "轨迹")):
            criteria.append("trace_recorded")
        if any(marker in text for marker in ("tests_still_failing", "tests still failing", "测试仍然失败")):
            criteria.append("expected_failure_reason_tests_still_failing")
        if "empty or invalid" in text and "none" in text:
            criteria.append("date_empty_or_invalid_returns_none")
        if "valid iso" in text:
            criteria.append("date_valid_iso_preserved")
        if "decimal-safe" in text or "floating point arithmetic" in text:
            criteria.append("decimal_safe_arithmetic")
        if "rounded to two decimal" in text or "two decimal places" in text:
            criteria.append("money_rounded_to_cents")
        if "return strings" in text or "numeric values" in text:
            criteria.append("no_string_money_return")
        if "ceiling division" in text or "partially filled final page" in text:
            criteria.append("pagination_ceiling_division")
        if "not return less than one" in text or "less than one page" in text:
            criteria.append("pagination_minimum_one")
        if "strip surrounding whitespace" in text or "trimming surrounding whitespace" in text:
            criteria.append("email_strips_whitespace")
        if "empty string for blank" in text:
            criteria.append("email_blank_input_empty")
        if "remove punctuation" in text or "punctuation is removed" in text:
            criteria.append("slug_removes_punctuation")
        if "collapse repeated separators" in text or "repeated separators" in text:
            criteria.append("slug_collapses_separators")
        if "trim leading/trailing" in text or "leading/trailing separators" in text:
            criteria.append("slug_trims_outer_separators")
        if "less than or equal" in text:
            criteria.append("inventory_inclusive_threshold")
        if "skip the header" in text or "skipping the header" in text:
            criteria.append("csv_skips_header")
        if "ignore blank" in text:
            criteria.append("csv_ignores_blank_lines")
        if "preserve sign" in text or "correct sign" in text:
            criteria.append("timezone_preserves_sign")
        if "absolute hours/minutes" in text or "absolute minutes" in text:
            criteria.append("timezone_uses_absolute_minutes")
        if "zero-pad" in text or "zero-padded" in text:
            criteria.append("timezone_zero_pads")
        if "attempt 1" in text and "base delay" in text:
            criteria.append("retry_first_attempt_base_delay")
        if "cap subsequent" in text or "respect the cap" in text:
            criteria.append("retry_caps_backoff")
        return criteria

    @staticmethod
    def _changed_paths(diff_text: str) -> set[str]:
        paths: set[str] = set()
        for line in diff_text.splitlines():
            if line.startswith("+++ b/"):
                path = line[6:].strip()
                if path != "/dev/null":
                    paths.add(path)
        return paths

    @staticmethod
    def _added_lines(diff_text: str, path: str | None = None) -> list[str]:
        lines: list[str] = []
        current_path: str | None = None
        for line in diff_text.splitlines():
            if line.startswith("diff --git "):
                current_path = None
                continue
            if line.startswith("+++ b/"):
                current_path = line[6:].strip()
                continue
            if line.startswith("+") and not line.startswith("+++"):
                if path is None or current_path == path:
                    lines.append(line[1:])
        return lines

    @staticmethod
    def _path_diff_text(diff_text: str, path: str | None) -> str:
        if not path:
            return diff_text
        chunks: list[str] = []
        current_path: str | None = None
        collecting = False
        for line in diff_text.splitlines():
            if line.startswith("diff --git "):
                collecting = False
                current_path = None
            if line.startswith("+++ b/"):
                current_path = line[6:].strip()
                collecting = current_path == path
            if collecting:
                chunks.append(line)
        return "\n".join(chunks)

    @staticmethod
    def _normalize_code(value: str) -> str:
        return re.sub(r"\s+", " ", value.lower())

    @staticmethod
    def _slug_collapses_separators(source: str) -> bool:
        return (
            "-+" in source
            or "while" in source
            or (
                "re.sub" in source
                and (r"\s+" in source or r"[\s\W_]+" in source or r"[\s\W]+" in source)
                and ("'-'" in source or '"-"' in source)
            )
        )

    @staticmethod
    def _decimal_safe_arithmetic(source: str) -> bool:
        """Require Decimal quantization rather than float-based round(..., 2)."""
        return "decimal" in source and "quantize" in source

    @staticmethod
    def _pagination_minimum_one(source: str) -> bool:
        return "max(1" in source or ("== 0" in source and "return 1" in source)

    @staticmethod
    def _contains_hardcoded_test_values(value: str) -> bool:
        suspicious_literals = {
            "2026-08-27",
            "not-a-date",
            "USER@Example.COM",
            "Hello,   ResearchForge!",
            "Ada,10",
            "Grace,9",
        }
        return any(literal.lower() in value.lower() for literal in suspicious_literals)

    @staticmethod
    def _removed_assertions_from_tests(diff_text: str) -> bool:
        current_path = ""
        for line in diff_text.splitlines():
            if line.startswith("--- a/"):
                current_path = line[6:].strip()
                continue
            if line.startswith("-") and not line.startswith("---") and "assert" in line:
                if current_path.startswith("tests/") or current_path.startswith("test_"):
                    return True
        return False

    @staticmethod
    def _criterion_label(value: str) -> str:
        labels = {
            "all_tests_pass": "全部测试通过",
            "no_policy_violation": "无策略违规",
            "patch_applies_cleanly": "补丁干净应用",
            "failure_report_generated": "生成失败报告",
            "trace_recorded": "轨迹已记录",
            "no_test_changes": "未修改测试",
            "expected_failure_reason_tests_still_failing": "失败类型符合预期",
            "report_generated": "生成报告",
            "expected_source_file_modified": "修改了期望实现文件",
            "no_hardcoded_test_values": "未硬编码测试值",
            "no_assertion_relaxation": "未放宽断言",
            "no_string_money_return": "金额仍返回数值",
            "date_empty_or_invalid_returns_none": "日期空值/非法值返回 None",
            "date_valid_iso_preserved": "保留合法 ISO 日期解析",
            "decimal_safe_arithmetic": "使用 Decimal 安全计算金额",
            "money_rounded_to_cents": "金额按两位小数取整",
            "pagination_ceiling_division": "分页使用向上取整",
            "pagination_minimum_one": "分页最少返回一页",
            "email_strips_whitespace": "邮箱去除首尾空白并小写",
            "email_blank_input_empty": "空白邮箱返回空字符串",
            "slug_removes_punctuation": "Slug 移除标点",
            "slug_collapses_separators": "Slug 合并重复分隔符",
            "slug_trims_outer_separators": "Slug 去除首尾分隔符",
            "inventory_inclusive_threshold": "库存阈值包含等于边界",
            "csv_skips_header": "CSV 计数跳过表头",
            "csv_ignores_blank_lines": "CSV 计数忽略空行",
            "timezone_preserves_sign": "时区格式保留正负号",
            "timezone_uses_absolute_minutes": "时区使用绝对分钟数",
            "timezone_zero_pads": "时区小时分钟补零",
            "retry_first_attempt_base_delay": "首次重试使用基础延迟",
            "retry_caps_backoff": "重试退避受上限约束",
        }
        return labels.get(value, value)
