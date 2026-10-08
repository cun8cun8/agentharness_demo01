from io import BytesIO
from types import SimpleNamespace
import sys

import pytest

from app.config import get_settings
from app.domain.schemas import (
    Artifact,
    ArtifactType,
    CreateRepositoryConnectionRequest,
    CreateTaskRequest,
)
from app.infra.store import InMemoryStore, create_store


def test_json_snapshot_persists_core_records(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCHFORGE_PERSISTENCE", "1")
    monkeypatch.setenv("RESEARCHFORGE_STORE_BACKEND", "json")
    monkeypatch.setenv("RESEARCHFORGE_STORE_PATH", str(tmp_path / "store.json"))
    get_settings.cache_clear()

    first = InMemoryStore()
    task = first.create_task(
        CreateTaskRequest(
            title="Snapshot persistence task",
            repo_path=None,
            test_command="pytest",
            goal="Verify persistence.",
            execution_config={
                "task_kind": "date",
                "source_path": "src/date_parser.py",
            },
        )
    )
    run = first.create_run(
        task_id=task.id,
        agent_strategy_id="repair_baseline_v1",
        policy_version_id="policy_default_v1",
        model_name="mock-coding-agent",
    )

    get_settings.cache_clear()
    second = InMemoryStore()
    assert second.get_task(task.id) is not None
    assert second.get_task(task.id).execution_config["source_path"] == "src/date_parser.py"
    assert second.get_run(run.id) is not None
    assert second.list_events(run.id)[0].event_type == "run.created"

    get_settings.cache_clear()


def test_run_captures_connected_project_context(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCHFORGE_PERSISTENCE", "0")
    monkeypatch.setenv("RESEARCHFORGE_STORE_BACKEND", "memory")
    get_settings.cache_clear()
    store = InMemoryStore()
    repository = store.create_repository_connection(
        CreateRepositoryConnectionRequest(
            name="payments-service",
            provider="github",
            url="https://github.com/example/payments-service.git",
        )
    )
    task = store.create_task(
        CreateTaskRequest(
            title="Fix checkout total",
            repo_path=f"/workspace/repositories/{repository.id}",
            test_command="pytest -q",
            goal="Fix the checkout total calculation.",
            execution_config={"repository_id": repository.id},
        )
    )

    run = store.create_run(
        task.id,
        "repair_baseline_v1",
        "policy_default_v1",
        "mock-coding-agent",
    )

    assert run.repository_id == repository.id
    assert run.project_name == "payments-service"
    assert run.repository_url == "https://github.com/example/payments-service.git"
    assert run.branch == "main"
    assert run.default_branch == "main"
    assert run.repo_path == task.repo_path
    get_settings.cache_clear()


def test_store_factory_rejects_postgres_without_dsn(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCHFORGE_PERSISTENCE", "1")
    monkeypatch.setenv("RESEARCHFORGE_STORE_BACKEND", "postgres")
    monkeypatch.delenv("RESEARCHFORGE_POSTGRES_DSN", raising=False)
    get_settings.cache_clear()

    with pytest.raises(RuntimeError, match="RESEARCHFORGE_POSTGRES_DSN"):
        create_store()

    monkeypatch.setenv("RESEARCHFORGE_STORE_BACKEND", "json")
    get_settings.cache_clear()


def test_snapshot_round_trip_preserves_strategy_runtime_config(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCHFORGE_PERSISTENCE", "0")
    monkeypatch.setenv("RESEARCHFORGE_STORE_BACKEND", "memory")
    get_settings.cache_clear()

    first = InMemoryStore()
    first.strategies["repair_with_critic_v3"].runtime_config["retry"] = False
    data = first._snapshot_data()

    second = InMemoryStore()
    second._restore_snapshot_data(data)

    assert second.get_strategy("repair_with_critic_v3").runtime_config["critic"] is True
    assert second.get_strategy("repair_with_critic_v3").runtime_config["retry"] is False

    get_settings.cache_clear()


def test_json_snapshot_externalizes_artifact_content(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCHFORGE_PERSISTENCE", "1")
    monkeypatch.setenv("RESEARCHFORGE_STORE_BACKEND", "json")
    monkeypatch.setenv("RESEARCHFORGE_STORE_PATH", str(tmp_path / "store.json"))
    monkeypatch.setenv("RESEARCHFORGE_ARTIFACT_STORE_BACKEND", "filesystem")
    monkeypatch.setenv("RESEARCHFORGE_ARTIFACT_STORE_PATH", str(tmp_path / "artifacts"))
    get_settings.cache_clear()

    first = InMemoryStore()
    task = first.create_task(
        CreateTaskRequest(
            title="Artifact persistence task",
            repo_path=None,
            test_command="pytest",
            goal="Verify artifact blob storage.",
            execution_config={
                "task_kind": "date",
                "source_path": "src/date_parser.py",
            },
        )
    )
    run = first.create_run(
        task_id=task.id,
        agent_strategy_id="repair_baseline_v1",
        policy_version_id="policy_default_v1",
        model_name="mock-coding-agent",
    )
    artifact = first.add_artifact(
        Artifact(
            id="artifact_blob_test_001",
            run_id=run.id,
            type=ArtifactType.REPORT,
            name="report.md",
            uri="/api/v1/artifacts/artifact_blob_test_001/content",
            content="hello blob storage",
            metadata={},
        )
    )

    store_json = (tmp_path / "store.json").read_text(encoding="utf-8")
    assert "hello blob storage" not in store_json
    assert artifact.metadata["content_path"].endswith("artifact_blob_test_001.txt")

    get_settings.cache_clear()
    second = InMemoryStore()
    loaded = second.get_artifact(artifact.id)
    assert loaded is not None
    assert loaded.content == "hello blob storage"
    assert loaded.metadata["content_path"].endswith("artifact_blob_test_001.txt")

    get_settings.cache_clear()


def test_restore_can_defer_external_artifact_hydration(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCHFORGE_PERSISTENCE", "1")
    monkeypatch.setenv("RESEARCHFORGE_STORE_BACKEND", "json")
    monkeypatch.setenv("RESEARCHFORGE_STORE_PATH", str(tmp_path / "store.json"))
    monkeypatch.setenv("RESEARCHFORGE_ARTIFACT_STORE_BACKEND", "filesystem")
    monkeypatch.setenv("RESEARCHFORGE_ARTIFACT_STORE_PATH", str(tmp_path / "artifacts"))
    get_settings.cache_clear()

    first = InMemoryStore()
    task = first.create_task(CreateTaskRequest(title="Deferred artifact task", goal="Verify lazy load."))
    run = first.create_run(task.id, "repair_baseline_v1", "policy_default_v1", "mock-coding-agent")
    artifact = first.add_artifact(
        Artifact(
            id="artifact_deferred_test_001",
            run_id=run.id,
            type=ArtifactType.REPORT,
            name="report.md",
            uri="/api/v1/artifacts/artifact_deferred_test_001/content",
            content="lazy artifact body",
        )
    )

    second = InMemoryStore()
    second._restore_snapshot_data(first._snapshot_data(), hydrate_artifacts=False)

    assert second.artifacts[artifact.id].content == ""
    assert second.get_artifact(artifact.id).content == "lazy artifact body"

    get_settings.cache_clear()


def test_s3_artifact_blob_store_round_trip_with_fake_boto3(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RESEARCHFORGE_PERSISTENCE", "0")
    monkeypatch.setenv("RESEARCHFORGE_STORE_BACKEND", "memory")
    monkeypatch.setenv("RESEARCHFORGE_ARTIFACT_STORE_BACKEND", "s3")
    monkeypatch.setenv("RESEARCHFORGE_ARTIFACT_STORE_BUCKET", "rf-artifacts")
    monkeypatch.setenv("RESEARCHFORGE_ARTIFACT_STORE_PREFIX", "runs")
    monkeypatch.setenv("TEST_S3_KEY", "key")
    monkeypatch.setenv("TEST_S3_SECRET", "secret")
    get_settings.cache_clear()

    class FakeS3Client:
        def __init__(self) -> None:
            self.objects: dict[tuple[str, str], bytes] = {}

        def head_bucket(self, Bucket: str) -> None:
            if Bucket != "rf-artifacts":
                raise RuntimeError("bucket missing")

        def put_object(self, Bucket: str, Key: str, Body: bytes, ContentType: str | None = None) -> None:
            self.objects[(Bucket, Key)] = Body

        def get_object(self, Bucket: str, Key: str) -> dict[str, object]:
            return {"Body": BytesIO(self.objects[(Bucket, Key)])}

    fake_client = FakeS3Client()
    monkeypatch.setitem(
        sys.modules,
        "boto3",
        SimpleNamespace(client=lambda *args, **kwargs: fake_client),
    )

    store = InMemoryStore()
    probe = store.artifact_blob_store.probe()
    assert probe["ready"] is True

    task = store.create_task(
        CreateTaskRequest(
            title="S3 artifact task",
            repo_path=None,
            test_command="pytest",
            goal="Verify S3 artifact storage.",
            execution_config={"task_kind": "date", "source_path": "src/date_parser.py"},
        )
    )
    run = store.create_run(
        task_id=task.id,
        agent_strategy_id="repair_baseline_v1",
        policy_version_id="policy_default_v1",
        model_name="mock-coding-agent",
    )
    artifact = store.add_artifact(
        Artifact(
            id="artifact_s3_test_001",
            run_id=run.id,
            type=ArtifactType.REPORT,
            name="report.md",
            uri="/api/v1/artifacts/artifact_s3_test_001/content",
            content="hello s3 storage",
            metadata={},
        )
    )

    assert artifact.metadata["content_uri"] == "s3://rf-artifacts/runs/workspace_default/ar/ti/artifact_s3_test_001.txt"
    assert artifact.metadata["content_key"] == "runs/workspace_default/ar/ti/artifact_s3_test_001.txt"
    assert store.get_artifact(artifact.id).content == "hello s3 storage"

    get_settings.cache_clear()


def test_store_seeds_gateway_models_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCHFORGE_MODEL_NAME", "gpt-4.1-mini")
    monkeypatch.setenv("RESEARCHFORGE_RESEARCH_MODEL_NAME", "gpt-4.1")
    monkeypatch.setenv("RESEARCHFORGE_MODEL_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("RESEARCHFORGE_MODEL_API_KEY_ENV", "TEST_OPENAI_API_KEY")
    monkeypatch.setenv("RESEARCHFORGE_MODEL_CONTEXT_WINDOW", "64000")
    monkeypatch.setenv("RESEARCHFORGE_MODEL_COST_PER_1K_TOKENS", "0.01")
    get_settings.cache_clear()

    store = InMemoryStore()

    coding_model, coding_reason = store.select_model_config("coding", strict=True)
    research_model, research_reason = store.select_model_config("research", strict=True)

    assert coding_model.provider == "openai_compatible"
    assert coding_model.model_name == "gpt-4.1-mini"
    assert coding_model.config["source"] == "environment"
    assert coding_model.config["base_url"] == "https://example.test/v1"
    assert coding_model.config["api_key_env"] == "TEST_OPENAI_API_KEY"
    assert coding_reason == "按任务类型选择可用且成本最低的 active 模型。"

    assert research_model.provider == "openai_compatible"
    assert research_model.model_name == "gpt-4.1"
    assert research_model.config["source"] == "environment"
    assert research_reason == "按任务类型选择可用且成本最低的 active 模型。"

    get_settings.cache_clear()


def test_store_seeds_multiple_models_and_qwen_preset_from_catalog(monkeypatch) -> None:
    monkeypatch.setenv(
        "RESEARCHFORGE_MODEL_CATALOG",
        '[{"id":"qwen_coding","provider":"qwen","model_name":"qwen-plus","role":"coding","api_key_env":"DASHSCOPE_API_KEY","strategy_ids":["repair_with_critic_v3"]},'
        '{"id":"research_gateway","provider":"openai_compatible","model_name":"research-model","role":"research","base_url":"https://research.example/v1","api_key_env":"RESEARCH_KEY"}]',
    )
    monkeypatch.setenv("RESEARCHFORGE_MODEL_PROVIDER", "openai_compatible")
    get_settings.cache_clear()

    catalog_store = InMemoryStore()

    coding_model, _ = catalog_store.select_model_config("coding", strategy_id="repair_with_critic_v3", strict=True)
    research_model, _ = catalog_store.select_model_config("research", strict=True)

    assert coding_model.id == "qwen_coding"
    assert coding_model.provider == "qwen"
    assert coding_model.config["api_key_env"] == "DASHSCOPE_API_KEY"
    assert coding_model.config["strategy_ids"] == ["repair_with_critic_v3"]
    assert research_model.config["base_url"] == "https://research.example/v1"
    assert research_model.config["api_key_env"] == "RESEARCH_KEY"
