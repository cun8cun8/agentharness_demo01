from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from app.config import get_settings

from app.domain.schemas import Budget, CreateTaskRequest, TaskResponse, TaskType
from app.agent.task_profiles import patch_for_kind, source_path_for_kind


WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BENCHMARK = "coding_golden_v1"

TITLE_ZH = {
    "coding_fix_001": "修复日期解析边界问题",
    "coding_fix_002": "修复价格计算四舍五入",
    "coding_fix_003": "修复分页边界错误",
    "coding_fix_004": "强制失败恢复样例",
    "task_005_email_normalizer": "规范化用户邮箱地址",
    "task_006_slug_generator": "修复短链接标识的标点处理",
    "task_007_inventory_threshold": "修复库存补货阈值",
    "task_008_csv_counter": "修复 CSV 行计数",
    "task_009_timezone_formatter": "修复时区偏移格式化",
    "task_010_retry_backoff": "修复重试退避计算",
}

GOAL_ZH = {
    "coding_fix_001": "修复失败的日期解析测试，不修改测试期望。",
    "coding_fix_002": "修复失败的价格计算测试，不修改测试期望。",
    "coding_fix_003": "修复分页边界行为，不修改测试。",
    "coding_fix_004": "补丁后强制失败，用于演示失败报告。",
    "task_005_email_normalizer": "修复邮箱规范化中的空白和大小写处理，不修改测试。",
    "task_006_slug_generator": "修复短链接标识生成中的标点和重复分隔符处理。",
    "task_007_inventory_threshold": "修复精确边界值上的补货阈值行为。",
    "task_008_csv_counter": "修复 CSV 行计数，正确处理表头和空行。",
    "task_009_timezone_formatter": "修复负偏移和补零场景下的时区偏移格式化。",
    "task_010_retry_backoff": "修复重试退避计算，确保上限和首次重试正确。",
}

TAGS_BY_ID = {
    "task_005_email_normalizer": ["python", "pytest", "normalization"],
    "task_006_slug_generator": ["python", "pytest", "strings"],
    "task_007_inventory_threshold": ["python", "pytest", "business_logic"],
    "task_008_csv_counter": ["python", "pytest", "csv"],
    "task_009_timezone_formatter": ["python", "pytest", "datetime"],
    "task_010_retry_backoff": ["python", "pytest", "algorithms"],
}

TASK_EXECUTION_PROFILES: dict[str, dict[str, Any]] = {
    "coding_fix_001": {
        "task_kind": "date",
        "failure_observation": "失败指向日期解析边界：空字符串和非法格式。",
    },
    "coding_fix_002": {
        "task_kind": "price",
        "failure_observation": "失败指向价格计算中的四舍五入行为。",
    },
    "coding_fix_003": {
        "task_kind": "pagination",
        "failure_observation": "失败指向分页逻辑中的边界计算错误。",
    },
    "coding_fix_004": {
        "task_kind": "force_failure",
        "failure_observation": "该任务用于演示补丁后验证失败的报告链路。",
        "force_failure": True,
    },
    "task_005_email_normalizer": {
        "task_kind": "email",
        "failure_observation": "失败指向邮箱规范化中的空白和大小写处理。",
    },
    "task_006_slug_generator": {
        "task_kind": "slug",
        "failure_observation": "失败指向短链接标识生成中的标点和重复分隔符处理。",
    },
    "task_007_inventory_threshold": {
        "task_kind": "inventory",
        "failure_observation": "失败指向精确边界值上的补货阈值行为。",
    },
    "task_008_csv_counter": {
        "task_kind": "csv",
        "failure_observation": "失败指向 CSV 行计数对表头和空行的处理。",
    },
    "task_009_timezone_formatter": {
        "task_kind": "timezone",
        "failure_observation": "失败指向负偏移和补零场景下的时区偏移格式化。",
    },
    "task_010_retry_backoff": {
        "task_kind": "retry",
        "failure_observation": "失败指向重试退避计算的首次重试和上限行为。",
    },
}


@dataclass(frozen=True)
class GoldenTask:
    id: str
    title: str
    repo_path: str
    test_command: str
    goal: str
    difficulty: str
    tags: list[str]
    benchmark_name: str = DEFAULT_BENCHMARK
    task_dir: str = ""
    expected_path: str | None = None
    expected_text: str = ""
    success_criteria: list[str] = field(default_factory=list)
    timeout_seconds: int = 120
    max_steps: int = 20
    execution_config: dict[str, Any] = field(default_factory=dict)
    raw_title: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "raw_title": self.raw_title,
            "repo_path": self.repo_path,
            "test_command": self.test_command,
            "test_timeout_seconds": self.timeout_seconds,
            "goal": self.goal,
            "difficulty": self.difficulty,
            "tags": self.tags,
            "benchmark_name": self.benchmark_name,
            "task_dir": self.task_dir,
            "expected_path": self.expected_path,
            "expected_text": self.expected_text,
            "success_criteria": self.success_criteria,
            "execution_config": self.execution_config,
        }

    def to_task_request(self) -> CreateTaskRequest:
        execution_config = {
            **self.execution_config,
            "success_criteria": list(self.success_criteria),
            "expected_text": self.expected_text,
        }
        return CreateTaskRequest(
            type=TaskType.CODING,
            title=self.title,
            repo_path=self.repo_path,
            test_command=self.test_command,
            test_timeout_seconds=self.timeout_seconds,
            goal=self.goal,
            execution_config=execution_config,
            budget=Budget(max_steps=self.max_steps),
        )

    def sync_task(self, task: TaskResponse) -> TaskResponse:
        task.type = TaskType.CODING
        task.title = self.title
        task.repo_path = self.repo_path
        task.test_command = self.test_command
        task.test_timeout_seconds = self.timeout_seconds
        task.goal = self.goal
        task.execution_config = {
            **self.execution_config,
            "success_criteria": list(self.success_criteria),
            "expected_text": self.expected_text,
        }
        task.budget = Budget(max_steps=self.max_steps)
        return task


def list_golden_tasks(benchmark_name: str = DEFAULT_BENCHMARK) -> list[GoldenTask]:
    discovered = _discover_golden_tasks(benchmark_name)
    if discovered:
        return discovered
    if get_settings().strict_benchmarks:
        return []
    return _fallback_tasks()


def find_golden_task_for_repo(repo_path: str | None) -> GoldenTask | None:
    if not repo_path:
        return None
    normalized = Path(repo_path).as_posix().lower()
    for golden in list_golden_tasks():
        if Path(golden.repo_path).as_posix().lower() == normalized:
            return golden
    return None


def _discover_golden_tasks(benchmark_name: str) -> list[GoldenTask]:
    benchmark_root = WORKSPACE_ROOT / "benchmarks" / benchmark_name
    if not benchmark_root.exists():
        return []

    items: list[GoldenTask] = []
    for yaml_path in sorted(benchmark_root.glob("task_*/task.yaml")):
        task_dir = yaml_path.parent
        data = _read_yaml(yaml_path)
        task_id = str(data.get("id") or task_dir.name)
        raw_title = str(data.get("title") or task_dir.name.replace("_", " ").title())
        repo_path = _relative_workspace_path(task_dir, str(data.get("repo_path") or "./repo"))
        expected_path = task_dir / "expected.md"
        success_criteria = data.get("success_criteria") or ["all_tests_pass", "no_policy_violation", "patch_applies_cleanly"]
        if not isinstance(success_criteria, list):
            success_criteria = [str(success_criteria)]
        tags = data.get("tags") or TAGS_BY_ID.get(task_id) or ["python", "pytest"]
        if not isinstance(tags, list):
            tags = [str(tags)]
        test_command = str(data.get("test_command") or data.get("entrypoint") or "pytest")
        max_steps = int(data.get("max_steps") or 20)
        timeout_seconds = int(data.get("timeout_seconds") or 120)
        execution_config = _build_execution_config(
            task_id,
            task_dir / "repo",
            data.get("execution_config"),
        )
        items.append(
            GoldenTask(
                id=task_id,
                title=TITLE_ZH.get(task_id, raw_title),
                raw_title=raw_title,
                repo_path=repo_path,
                test_command=test_command,
                timeout_seconds=timeout_seconds,
                goal=GOAL_ZH.get(task_id, str(data.get("goal") or f"修复 {raw_title} 对应的失败测试。")),
                difficulty=str(data.get("difficulty") or "level_1"),
                tags=[str(tag) for tag in tags],
                benchmark_name=benchmark_name,
                task_dir=_relative_workspace_path(task_dir, "."),
                expected_path=_relative_workspace_path(task_dir, "expected.md") if expected_path.exists() else None,
                expected_text=expected_path.read_text(encoding="utf-8") if expected_path.exists() else "",
                success_criteria=[str(item) for item in success_criteria],
                max_steps=max_steps,
                execution_config=execution_config,
            )
        )
    return items


def _read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _relative_workspace_path(task_dir: Path, value: str) -> str:
    candidate = Path(value)
    resolved = candidate if candidate.is_absolute() else (task_dir / candidate)
    try:
        return resolved.resolve().relative_to(WORKSPACE_ROOT).as_posix()
    except ValueError:
        return str(resolved.resolve())


def _build_execution_config(
    task_id: str,
    repo_root: Path,
    yaml_config: Any = None,
) -> dict[str, Any]:
    config = dict(TASK_EXECUTION_PROFILES.get(task_id, {}))
    if isinstance(yaml_config, dict):
        config.update(yaml_config)
    task_kind = str(config.get("task_kind") or "").strip().lower() or "date"
    config["task_kind"] = task_kind

    source_path = str(config.get("source_path") or "").strip()
    if not source_path:
        source_path = _infer_source_path(repo_root) or source_path_for_kind(task_kind)
    if source_path:
        config["source_path"] = source_path

    if get_settings().strict_benchmarks or not get_settings().allow_mock_models:
        for key in ("patch", "retry_patch", "force_failure", "failure_observation"):
            config.pop(key, None)
        config["evaluation_mode"] = "model_only"
        return config

    patch = str(config.get("patch") or "").strip()
    if not patch:
        patch = patch_for_kind(task_kind)
    config["patch"] = patch
    config.setdefault("retry_patch", patch)
    return config


def _infer_source_path(repo_root: Path) -> str | None:
    src_root = repo_root / "src"
    if not src_root.exists():
        return None
    candidates = [
        path.relative_to(repo_root).as_posix()
        for path in sorted(src_root.glob("*.py"))
        if path.is_file() and path.name != "__init__.py"
    ]
    if not candidates:
        return None
    return candidates[0]


def _fallback_tasks() -> list[GoldenTask]:
    return [
        GoldenTask(
            id="coding_fix_001",
            title="修复日期解析边界问题",
            repo_path="benchmarks/coding_golden_v1/task_001_date_parser/repo",
            test_command="pytest",
            goal="修复失败的日期解析测试，不修改测试期望。",
            difficulty="level_1",
            tags=["python", "pytest", "edge_case"],
            execution_config={
                "task_kind": "date",
                "source_path": "src/date_parser.py",
                "failure_observation": "失败指向日期解析边界：空字符串和非法格式。",
                "patch": patch_for_kind("date"),
                "retry_patch": patch_for_kind("date"),
            },
        )
    ]
