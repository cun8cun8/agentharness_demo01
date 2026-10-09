from app.evals.benchmark_runner import BenchmarkRunner
from app.agent.runtime import AgentRuntime
from app.benchmarks.catalog import list_golden_tasks
from app.infra.store import InMemoryStore
import asyncio
from app.domain.schemas import CreateEvaluationRunRequest, RunStatus


def test_slug_criterion_accepts_regex_separator_collapse() -> None:
    source = "value = re.sub(r'\\s+', '-', value)"

    assert BenchmarkRunner._slug_collapses_separators(source)


def test_slug_criterion_accepts_non_word_separator_regex() -> None:
    source = "value = re.sub(r'[\\s\\W_]+', '-', value)"

    assert BenchmarkRunner._slug_collapses_separators(source)


def test_price_contract_requires_decimal_safe_arithmetic() -> None:
    decimal_safe = "from decimal import Decimal\nreturn Decimal('2.675').quantize(Decimal('0.01'))"
    float_rounding = "return round(amount * (1 + tax_rate), 2)"

    assert BenchmarkRunner._decimal_safe_arithmetic(
        BenchmarkRunner._normalize_code(decimal_safe)
    )
    assert not BenchmarkRunner._decimal_safe_arithmetic(
        BenchmarkRunner._normalize_code(float_rounding)
    )


def test_pagination_contract_accepts_explicit_empty_collection_guard() -> None:
    source = "if total_items == 0:\n    return 1\nreturn (total_items + page_size - 1) // page_size"

    assert BenchmarkRunner._pagination_minimum_one(BenchmarkRunner._normalize_code(source))


def test_golden_requirements_are_available_to_patch_generation() -> None:
    golden = next(item for item in list_golden_tasks() if item.id == "coding_fix_002")
    store = InMemoryStore()
    task = store.create_task(golden.to_task_request())

    requirements = AgentRuntime(store=store)._golden_requirements(task)

    assert "decimal-safe arithmetic" in requirements


def test_cancelled_batch_does_not_start_another_model_run():
    store = InMemoryStore()
    tasks = [store.create_task(golden.to_task_request()) for golden in list_golden_tasks()[:2]]
    job = store.create_job(kind="coding_acceptance", resource_id="coding_golden_v1")
    store.update_job(job.id, "running")
    executed = []
    class Runtime:
        async def execute_run(self, run_id):
            executed.append(run_id)
            store.update_job(job.id, "cancelled")
            return store.update_run(run_id, status=RunStatus.CANCELLED)
    runner = BenchmarkRunner(store, Runtime())
    result = asyncio.run(runner.run(CreateEvaluationRunRequest(
        benchmark_name="coding_golden_v1", task_ids=[t.id for t in tasks],
        agent_strategy_id="repair_with_critic_v3", policy_version_id="policy_default_v1",
        model_name="mock-coding-agent"), parent_job_id=job.id))
    assert len(executed) == 1
    assert result.status == RunStatus.CANCELLED
    assert store.read_job(job.id).status == "cancelled"
