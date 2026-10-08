import importlib.util

from fastapi import APIRouter, HTTPException, Query, Request
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.benchmarks.catalog import list_golden_tasks
from app.config import get_settings
from app.infra.store import store
from app.services.job_queue import job_queue
from app.services.event_bus import event_publisher
from app.services.sandbox_runner import sandbox_runner
from app.tools.registry import default_registry

router = APIRouter(tags=["system"])


@router.get("/system/ha")
async def high_availability_status():
    from app.services.ha import snapshot
    return await snapshot()


@router.get("/system/dependencies")
async def system_dependencies(request: Request):
    from app.api.context import is_admin
    from app.services.dependency_probe import probe_dependencies
    if not is_admin(request):
        raise HTTPException(403, "ADMIN_REQUIRED")
    return await probe_dependencies(store)


@router.post("/secrets/{env_name}/rotate")
async def rotate_credential(env_name: str, request: Request):
    import asyncio
    import os
    from app.api.context import current_user
    from app.services.secrets import rotate_secret
    allowed = {name.strip() for name in os.getenv("RESEARCHFORGE_ROTATABLE_SECRET_ENVS", "").split(",") if name.strip()}
    if env_name not in allowed:
        raise HTTPException(403, "SECRET_ROTATION_NOT_ALLOWED")
    try:
        result = await asyncio.to_thread(rotate_secret, env_name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "SECRET_ROTATION_FAILED") from exc
    store.add_audit_log(action="secret.rotate", resource_type="secret_reference", resource_id=env_name,
                         actor_id=current_user(request).id, decision="requested")
    return result


@router.get("/system/config")
async def system_config() -> dict[str, object]:
    settings = get_settings()
    data = settings.model_dump()
    for key in (
        "postgres_dsn",
        "redis_url",
        "model_gateway_base_url",
        "artifact_store_endpoint_url",
        "event_bus_url",
        "embedding_base_url",
        "neo4j_uri",
        "otel_endpoint",
    ):
        if data.get(key):
            data[key] = _redact_connection_string(str(data[key]))
    for key in ("api_key",):
        if data.get(key):
            data[key] = "***configured***"
    return data


@router.get("/system/storage")
async def system_storage() -> dict[str, object]:
    settings = get_settings()
    backend = settings.store_backend.strip().lower()
    storage_details = store.describe_storage()
    artifact_store = store.artifact_blob_store.probe()
    return {
        "backend": settings.store_backend,
        "persistent": settings.persistence_enabled and (
            backend == "json" or (backend == "postgres" and bool(settings.postgres_dsn))
        ),
        "snapshot_mode": storage_details.get("mode", "postgres") if backend == "postgres" else "file" if backend == "json" else "memory",
        "path": settings.store_path if backend == "json" else None,
        "postgres_configured": bool(settings.postgres_dsn),
        "postgres_migrations": storage_details.get("postgres_migrations"),
        "artifact_store_backend": artifact_store["backend"],
        "artifact_store_path": artifact_store["path"],
        "artifact_store_bucket": artifact_store["bucket"],
        "artifact_store_prefix": artifact_store["prefix"],
        "artifact_store_endpoint_url": _redact_connection_string(artifact_store["endpoint_url"]),
        "artifact_store_region": artifact_store["region"],
        "artifact_store_use_ssl": artifact_store["use_ssl"],
        "artifact_store_server_side_encryption": artifact_store.get("server_side_encryption"),
        "artifact_store_workspace_prefix": artifact_store.get("workspace_prefix"),
        "artifact_store_persistent": artifact_store["enabled"],
        "artifact_store_ready": artifact_store["ready"],
        "artifact_store_message": artifact_store["message"],
        "typed_repository_connections": bool(storage_details.get("typed_repository_connections")),
        "workflow_backend": settings.workflow_backend,
        "temporal_address": _redact_connection_string(settings.temporal_address),
        "temporal_namespace": settings.temporal_namespace,
        "temporal_task_queue": settings.temporal_task_queue,
    }


@router.get("/system/readiness")
async def system_readiness() -> dict[str, object]:
    settings = get_settings()
    backend = settings.store_backend.strip().lower()
    tools = {tool.name for tool in default_registry.list_tools()}
    sandbox = sandbox_runner.probe()
    active_strategies = [
        strategy
        for strategy in store.strategies.values()
        if strategy.status == "active"
    ]
    active_policies = [
        policy
        for policy in store.policies.values()
        if policy.status == "active"
    ]
    enabled_extensions = [
        extension
        for extension in store.extension_manifests.values()
        if extension.status == "enabled"
    ]
    active_models = store.list_model_configs(status="active")
    golden_tasks = list_golden_tasks()
    evaluation_runs = store.list_evaluation_runs()
    release_gates = store.list_release_gates()
    trace_items = store.list_trace_dataset_items()
    preference_pairs = store.list_preference_pairs()
    memory_items = store.list_memory_items()
    repository_connections = store.list_repository_connections()
    queue_snapshot = await job_queue.snapshot()
    event_bus_snapshot = event_publisher.snapshot()
    queue_worker_external = (
        queue_snapshot["backend"] == "redis"
        and not bool(queue_snapshot.get("worker_enabled"))
    )
    queue_ready = (
        queue_snapshot["backend"] != "redis"
        or (
            bool(queue_snapshot["connected"])
            and (
                bool(queue_snapshot["worker_running"])
                or queue_worker_external
            )
        )
    )
    queue_evidence = (
        f"{queue_snapshot['backend']} / "
        f"{'已连接' if queue_snapshot['connected'] else '未连接'} / "
        f"{'外置 Worker' if queue_worker_external else ('运行中' if queue_snapshot['worker_running'] else '已停止')}"
    )
    storage_details = store.describe_storage()
    artifact_store = store.artifact_blob_store.probe()
    migration_summary = storage_details.get("postgres_migrations") if isinstance(storage_details, dict) else None
    workflow_backend = settings.workflow_backend.strip().lower()
    workflow_ready = (
        queue_ready
        if workflow_backend == "queue"
        else workflow_backend == "temporal"
        and bool(settings.temporal_address)
        and importlib.util.find_spec("temporalio") is not None
    )
    latest_evaluation = evaluation_runs[0] if evaluation_runs else None
    checks = [
        _check("create_task", "能创建任务", True, "POST /api/v1/tasks"),
        _check("start_run", "能启动 Agent Run", True, "POST /api/v1/tasks/{task_id}/runs"),
        _check("baseline_tests", "能运行基线测试", "test.run" in tools, "tool:test.run"),
        _check("read_files", "能读取相关文件", "file.read" in tools, "tool:file.read"),
        _check("generate_patch", "能生成并应用 patch", "file.write_patch" in tools, "tool:file.write_patch"),
        _check("rerun_tests", "能重跑验证测试", "test.run" in tools, "tool:test.run"),
        _check("show_diff", "能展示 Diff", "git.diff" in tools, "tool:git.diff"),
        _check("write_report", "能生成报告", "report.write" in tools, "tool:report.write"),
        _check("record_trace", "能记录完整 Trace", True, "agent_steps/tool_calls/artifacts"),
        _check("block_dangerous_commands", "能拦截危险命令", bool(active_policies), "policy_default_v1"),
        _check("golden_tasks", "已加载 10 个 Golden Task", len(golden_tasks) >= 10, f"{len(golden_tasks)} tasks"),
        _check("compare_strategies", "能对比策略版本", len(active_strategies) >= 2, f"{len(active_strategies)} active strategies"),
        _check("model_catalog", "已配置可用模型", len(active_models) >= 1, f"{len(active_models)} active models"),
        _check("model_route", "能路由到活跃模型", _can_route_model(), "store.select_model_config(task_type=coding)"),
        _check("sandbox_ready", "沙箱可用", bool(sandbox.get("ready")), f"{sandbox.get('backend')} / {sandbox.get('version') or 'unknown'}"),
        _check("release_gate", "能执行发布门禁", True, "POST /api/v1/evaluations/release-gate"),
        _check("release_history", "能查看策略发布历史", len(release_gates) >= 1, f"{len(release_gates)} gates"),
        _check("label_trace", "能标注 Trace", len(trace_items) >= 1, f"{len(trace_items)} trace items"),
        _check("export_dataset", "能导出数据候选", len(trace_items) >= 1, "GET /api/v1/datasets/export"),
        _check("evaluation_report", "能导出完整评测验收报告", len(evaluation_runs) >= 1, f"{len(evaluation_runs)} evaluation runs"),
        _check("approval_resume", "能审批并恢复运行", True, "POST /api/v1/runs/{run_id}/resume"),
        _check("extension_governance", "能治理扩展和 Hook", bool(enabled_extensions), f"{len(enabled_extensions)} enabled extensions"),
        _check("model_fallback_diagnostics", "能记录模型重试和回退原因", True, "ModelInvokeResponse.attempts/fallback_reason"),
        _check("preference_pairs", "能管理偏好对", len(preference_pairs) >= 1, f"{len(preference_pairs)} preference pairs"),
        _check("memory_items", "能管理记忆项", len(memory_items) >= 1, f"{len(memory_items)} memory items"),
        _check("repository_connections", "已登记仓库连接", len(repository_connections) >= 1, f"{len(repository_connections)} repository connections"),
        _check(
            "event_bus",
            "跨进程事件总线可用",
            event_bus_snapshot["backend"] != "redis" or bool(event_bus_snapshot["connected"]),
            f"{event_bus_snapshot['backend']} / {event_bus_snapshot['channel']} / "
            f"{'已连接' if event_bus_snapshot['connected'] else '未连接'}",
        ),
        _check(
            "job_queue",
            "任务队列可用",
            workflow_ready,
            (
                queue_evidence
                if workflow_backend == "queue"
                else f"{workflow_backend} / {'SDK 已安装' if importlib.util.find_spec('temporalio') else 'SDK 未安装'} / "
                f"{'已配置地址' if settings.temporal_address else '未配置地址'}"
            ),
        ),
        _check(
            "typed_repository_storage",
            "仓库连接使用 typed 持久化边界",
            backend != "postgres" or bool(storage_details.get("typed_repository_connections")),
            "typed repository_connections" if storage_details.get("typed_repository_connections") else "generic store",
        ),
        _check(
            "persistent_store",
            "已配置持久化存储",
            bool(settings.persistence_enabled),
            "json snapshot / postgres snapshot",
        ),
        _check(
            "postgres_migrations",
            "PostgreSQL 迁移机制可用",
            backend != "postgres"
            or (
                isinstance(migration_summary, dict)
                and int(migration_summary.get("available", 0) or 0) >= 1
            ),
            (
                "当前未启用 PostgreSQL"
                if backend != "postgres"
                else (
                    f"{int(migration_summary.get('recorded', migration_summary.get('applied', 0))) if isinstance(migration_summary, dict) else 0} / "
                    f"{int(migration_summary.get('available', 0)) if isinstance(migration_summary, dict) else 0} migrations"
                )
            ),
        ),
        _check(
            "artifact_blob_store",
            "能外部化大体量产物",
            bool(artifact_store.get("ready")),
            str(artifact_store.get("message") or artifact_store.get("backend") or "unknown"),
        ),
    ]
    passed = sum(1 for item in checks if item["passed"])
    return {
        "status": "ready" if passed == len(checks) else "partial",
        "passed": passed,
        "total": len(checks),
        "completion_rate": round(passed / len(checks), 4) if checks else 0,
        "checks": checks,
        "latest_evaluation_id": latest_evaluation.id if latest_evaluation else None,
        "event_bus": event_bus_snapshot,
        "next_recommended_action": (
            _next_recommended_action(checks, latest_evaluation is None)
        ),
    }


@router.get("/system/production-readiness")
async def production_readiness(
    request: Request,
    verify_dependencies: bool = Query(default=False),
    verify_runtime: bool = Query(default=False),
) -> dict[str, object]:
    """Report whether this instance has the required production configuration.

    This does not make external changes by default. ``verify_dependencies`` runs
    bounded dependency connection probes. ``verify_runtime`` additionally runs a
    fixed no-network sandbox command and no-inference model gateway probes.
    """
    from app.api.context import is_admin
    from app.services.dependency_probe import probe_dependencies
    from app.services.model_gateway import model_health
    from app.services.runtime_adapters import list_runtime_adapters
    from app.services.github_app import app_enabled
    from app.services.github_oauth import oauth_enabled
    from app.services.secrets import resolve_secret

    if not is_admin(request):
        raise HTTPException(403, "ADMIN_REQUIRED")

    settings = get_settings()
    storage_details = store.describe_storage()
    migration_summary = storage_details.get("postgres_migrations") if isinstance(storage_details, dict) else None
    artifact_store = store.artifact_blob_store.probe()
    queue_snapshot = await job_queue.snapshot()
    event_bus_snapshot = event_publisher.snapshot()
    sandbox = sandbox_runner.probe()
    active_models = store.list_model_configs(status="active")
    repository_connections = store.list_repository_connections()
    github_connections = [
        item for item in repository_connections
        if str(getattr(item, "provider", "")).strip().lower() == "github"
    ]
    try:
        github_auth_ready = any((
            app_enabled(settings),
            oauth_enabled(settings),
            any(bool(getattr(item, "credential_ref", None)) for item in github_connections),
        ))
    except Exception:
        github_auth_ready = False
    try:
        webhook_secret_ready = bool(resolve_secret(settings.github_webhook_secret_env))
    except Exception:
        webhook_secret_ready = False
    try:
        metrics_token_ready = bool(resolve_secret(settings.metrics_token_env))
    except Exception:
        metrics_token_ready = False
    webhook_url_ready = bool(
        settings.github_webhook_public_url
        and settings.github_webhook_public_url.lower().startswith("https://")
    )
    github_redirect_ready = bool(
        urlsplit(settings.github_oauth_redirect_uri).scheme == "https"
        and bool(urlsplit(settings.github_oauth_redirect_uri).hostname)
    )
    real_models = [model for model in active_models if model.provider.strip().lower() != "mock"]
    ready_real_models = [item for item in (model_health(model) for model in real_models) if item.healthy]
    from app.agent.external_backends import select_agent_backend

    configured_runtime_backend = settings.agent_backend.strip().lower().replace("-", "_")
    runtime_backend = select_agent_backend(configured_runtime_backend, settings)
    runtime_adapters = {
        str(item.get("id")): item for item in list_runtime_adapters()
    }
    runtime_adapter = runtime_adapters.get(runtime_backend, {})
    runtime_ready = runtime_backend == "langgraph" and runtime_adapter.get("status") in {
        "available",
        "active",
        "configured",
    }
    if runtime_backend in {"openhands", "openhands_cli", "mini_swe_agent"}:
        runtime_ready = bool(runtime_adapter.get("details", {}).get("ready"))
    postgres_migrations_ready = (
        settings.store_backend.strip().lower() == "postgres"
        and isinstance(migration_summary, dict)
        and int(migration_summary.get("recorded", migration_summary.get("applied", 0)) or 0)
        >= int(migration_summary.get("available", 0) or 0)
    )
    checks = [
        _check("production_environment", "运行环境为 production", settings.environment.lower() in {"production", "prod"}, settings.environment),
        _check("production_auth", "生产认证模式已启用", settings.auth_mode.lower() == "production", settings.auth_mode),
        _check("mock_models_disabled", "演示模型已禁用", not settings.allow_mock_models, str(settings.allow_mock_models)),
        _check("postgres_store", "PostgreSQL 存储已配置", settings.store_backend.strip().lower() == "postgres" and bool(settings.postgres_dsn), settings.store_backend),
        _check("postgres_migrations", "PostgreSQL 迁移已完成", postgres_migrations_ready, _migration_evidence(migration_summary)),
        _check("redis_queue", "Redis 任务队列已配置", settings.job_queue_backend.strip().lower() == "redis", settings.job_queue_backend),
        _check("external_worker", "独立 Worker 已启用", not settings.job_worker_enabled, "外置 Worker" if not settings.job_worker_enabled else "API 内嵌 Worker"),
        _check("agent_runtime", "生产 Agent Runtime 已配置", runtime_ready, f"{configured_runtime_backend} -> {runtime_backend} / {runtime_adapter.get('status', 'unavailable')}"),
        _check("isolated_sandbox", "隔离沙箱已配置", settings.sandbox_backend.strip().lower() in {"docker", "kubernetes"}, settings.sandbox_backend),
        _check(
            "sandbox_probe",
            "隔离沙箱可用",
            settings.sandbox_backend.strip().lower() in {"docker", "kubernetes"} and bool(sandbox.get("ready")),
            f"{sandbox.get('backend')} / {sandbox.get('version') or 'unknown'}",
        ),
        _check("external_artifacts", "外部产物存储已配置", artifact_store.get("backend") == "s3" and bool(artifact_store.get("ready")), str(artifact_store.get("message") or artifact_store.get("backend"))),
        _check("real_model", "已配置可用的非演示活跃模型", bool(ready_real_models), f"{len(ready_real_models)} ready / {len(real_models)} real / {len(active_models)} active"),
        _check(
            "github_repository_auth",
            "GitHub 仓库认证已配置",
            not github_connections or github_auth_ready,
            "未登记 GitHub 仓库" if not github_connections else ("App/OAuth/凭据已配置" if github_auth_ready else "未配置 GitHub App、OAuth 或仓库凭据"),
        ),
        _check(
            "github_webhook",
            "GitHub Webhook 已安全配置",
            not github_connections or (webhook_secret_ready and webhook_url_ready),
            "未登记 GitHub 仓库" if not github_connections else (
                "HTTPS 回调与签名密钥已配置"
                if webhook_secret_ready and webhook_url_ready
                else "需要 HTTPS 公网回调地址和 Webhook 签名密钥"
            ),
        ),
        _check(
            "github_oauth_redirect",
            "GitHub OAuth 回调地址使用 HTTPS",
            github_redirect_ready,
            settings.github_oauth_redirect_uri if github_redirect_ready else "需要 HTTPS 回调地址",
        ),
        _check(
            "metrics_auth",
            "Prometheus 指标抓取已配置独立密钥",
            metrics_token_ready,
            settings.metrics_token_env if metrics_token_ready else "指标密钥未解析",
        ),
        _check(
            "artifact_encryption",
            "对象存储启用服务端加密",
            artifact_store.get("backend") == "s3"
            and str(artifact_store.get("server_side_encryption") or "").lower() in {"aes256", "aws:kms"}
            and (str(artifact_store.get("server_side_encryption") or "").lower() != "aws:kms" or bool(settings.artifact_store_kms_key_id)),
            str(artifact_store.get("server_side_encryption") or "未配置"),
        ),
        _check("model_network", "模型网关网络已启用", settings.network_enabled, str(settings.network_enabled)),
        _check("event_bus", "跨进程事件总线已配置", event_bus_snapshot.get("backend") in {"redis", "kafka", "redpanda"}, str(event_bus_snapshot.get("backend"))),
        _check("telemetry", "OpenTelemetry 已启用", settings.otel_enabled, str(settings.otel_enabled)),
        _check(
            "queue_probe",
            "Redis 队列已连接",
            settings.job_queue_backend.strip().lower() == "redis" and bool(queue_snapshot.get("connected")),
            str(queue_snapshot.get("backend")),
        ),
    ]
    dependency_result = None
    if verify_dependencies:
        dependency_result = await probe_dependencies(store)
        checks.append(
            _check(
                "dependency_connectivity",
                "已验证外部依赖连接",
                dependency_result.get("status") == "ok",
                str(dependency_result.get("coverage") or "dependency_connectivity_only"),
            )
        )
    runtime_result = None
    if verify_runtime:
        sandbox_execution = sandbox_runner.execution_probe()
        model_connectivity = [
            model_health(model, verify_connectivity=True)
            for model in real_models
        ]
        ready_model_connectivity = [item for item in model_connectivity if item.healthy]
        runtime_result = {
            "sandbox": sandbox_execution,
            "models": [item.model_dump(mode="json") for item in model_connectivity],
        }
        checks.extend([
            _check(
                "sandbox_execution",
                "隔离沙箱可执行固定自检命令",
                bool(sandbox_execution.get("execution_ready")),
                str(sandbox_execution.get("reason") or sandbox_execution.get("message") or "unknown"),
            ),
            _check(
                "model_connectivity",
                "至少一个真实模型网关可达",
                bool(ready_model_connectivity),
                f"{len(ready_model_connectivity)} reachable / {len(real_models)} real",
            ),
        ])
    passed = sum(1 for item in checks if item["passed"])
    return {
        "status": "ready" if passed == len(checks) else "not_ready",
        "profile": "production",
        "verified_dependencies": verify_dependencies,
        "verified_runtime": verify_runtime,
        "passed": passed,
        "total": len(checks),
        "completion_rate": round(passed / len(checks), 4) if checks else 0,
        "checks": checks,
        "next_actions": _production_next_actions(checks),
        "dependencies": dependency_result,
        "runtime": runtime_result,
    }


def _check(check_id: str, label: str, passed: bool, evidence: str) -> dict[str, object]:
    return {
        "id": check_id,
        "label": label,
        "passed": bool(passed),
        "evidence": evidence,
    }


def _migration_evidence(summary: object) -> str:
    if not isinstance(summary, dict):
        return "migration metadata unavailable"
    recorded = int(summary.get("recorded", summary.get("applied", 0)) or 0)
    available = int(summary.get("available", 0) or 0)
    return f"{recorded} / {available} migrations"


def _production_next_actions(checks: list[dict[str, object]]) -> list[dict[str, str]]:
    """Turn failed readiness checks into non-secret operator actions."""
    guidance = {
        "production_environment": (
            "切换生产运行配置",
            "在 Worker 和 API 设置 RESEARCHFORGE_ENV=production。",
            "重启后重新执行生产检查。",
        ),
        "production_auth": (
            "启用生产认证",
            "设置 RESEARCHFORGE_AUTH_MODE=production，并从密钥管理服务注入管理员 API 密钥与会话密钥。",
            "GET /api/v1/auth/session 应拒绝未认证请求。",
        ),
        "mock_models_disabled": (
            "关闭演示模型",
            "设置 RESEARCHFORGE_ALLOW_MOCK_MODELS=0 和 RESEARCHFORGE_STRICT_BENCHMARKS=1。",
            "GET /api/v1/system/production-readiness 的该项应通过。",
        ),
        "postgres_store": (
            "连接 PostgreSQL",
            "设置 RESEARCHFORGE_STORE_BACKEND=postgres、RESEARCHFORGE_POSTGRES_DSN 和 RESEARCHFORGE_PERSISTENCE=1。",
            "GET /api/v1/system/storage 应显示 postgres 且持久化已启用。",
        ),
        "postgres_migrations": (
            "完成数据库迁移",
            "以部署身份启动 API，使迁移在目标 PostgreSQL 上完成。",
            "GET /api/v1/system/storage 的 postgres_migrations 应为全量完成。",
        ),
        "redis_queue": (
            "配置 Redis 队列",
            "设置 RESEARCHFORGE_JOB_QUEUE_BACKEND=redis 和 RESEARCHFORGE_REDIS_URL。",
            "GET /api/v1/system/dependencies 应报告 redis 为 ok。",
        ),
        "external_worker": (
            "部署独立 Worker",
            "设置 RESEARCHFORGE_JOB_WORKER_ENABLED=0，并部署 researchforge-worker 副本。",
            "GET /api/v1/system/readiness 应显示外置 Worker。",
        ),
        "agent_runtime": (
            "启用生产 Agent Runtime",
            "设置 RESEARCHFORGE_AGENT_BACKEND=langgraph，或配置已审查的 OpenHands/mini-SWE-agent 外部运行时。",
            "重新执行生产检查，确认 Agent Runtime 状态为 available/configured。",
        ),
        "isolated_sandbox": (
            "启用隔离沙箱",
            "设置 RESEARCHFORGE_SANDBOX_BACKEND=docker 或 kubernetes，并授予 Worker 最小化运行权限。",
            "GET /api/v1/sandbox/check?verify_execution=true 应通过。",
        ),
        "sandbox_probe": (
            "验证隔离沙箱",
            "检查沙箱镜像、容器运行时或 Kubernetes RBAC、命名空间与共享工作区配置。",
            "GET /api/v1/sandbox/check?verify_execution=true 应返回 execution_ready=true。",
        ),
        "sandbox_execution": (
            "执行沙箱运行时验收",
            "在不开放网络的条件下确认 Worker 可创建、等待和清理短生命周期沙箱。",
            "重新执行生产检查并附加 verify_runtime=true。",
        ),
        "external_artifacts": (
            "配置对象存储",
            "设置 S3 终端、桶、区域和最小权限运行身份；不要将访问密钥写入版本库。",
            "GET /api/v1/system/storage 应显示 artifact_store_ready=true。",
        ),
        "real_model": (
            "登记真实模型网关",
            "在模型工作台登记 OpenAI-compatible、Anthropic 或 Gemini 配置，并通过密钥环境变量或密钥 URI 注入凭据。",
            "GET /api/v1/models/health?verify_connectivity=true 至少应有一个真实模型健康。",
        ),
        "github_repository_auth": (
            "配置 GitHub 仓库认证",
            "配置 GitHub App 安装令牌、OAuth 凭据，或通过受控 Secret 注入仓库凭据。",
            "重新执行生产检查，确认 GitHub 仓库认证项通过。",
        ),
        "github_webhook": (
            "配置 GitHub Webhook",
            "设置 HTTPS 公网 Webhook 地址和 RESEARCHFORGE_GITHUB_WEBHOOK_SECRET，并在 GitHub App 中指向该地址。",
            "发送一次签名 Webhook 测试事件，确认返回 2xx 并产生审计记录。",
        ),
        "github_oauth_redirect": (
            "固定 GitHub OAuth 回调地址",
            "设置 RESEARCHFORGE_GITHUB_REDIRECT_URI=https://<受控域名>/，并在 GitHub OAuth/App 配置中使用完全相同的地址。",
            "重新执行生产检查，确认回调地址项通过。",
        ),
        "metrics_auth": (
            "配置指标抓取密钥",
            "通过 Secret 注入 RESEARCHFORGE_METRICS_TOKEN_ENV 指向的 Bearer 密钥，Prometheus 使用 Authorization: Bearer <token> 抓取指标。",
            "GET /api/v1/telemetry/metrics 在无密钥时应为 401，使用正确密钥时应为 200。",
        ),
        "artifact_encryption": (
            "启用产物加密",
            "将对象存储服务端加密设为 AES256 或 aws:kms；使用 aws:kms 时同时配置 KMS Key ID。",
            "GET /api/v1/system/storage 应显示 artifact_store_server_side_encryption。",
        ),
        "model_network": (
            "允许模型网关出站",
            "设置 RESEARCHFORGE_NETWORK_ENABLED=1；保持 RESEARCHFORGE_SANDBOX_NETWORK_ENABLED=0，除非任务明确需要网络。",
            "重新执行模型健康检查，确认没有提交推理请求。",
        ),
        "model_connectivity": (
            "验证模型连通性",
            "检查模型地址、TLS、出口策略和密钥引用，随后执行零推理模型目录探测。",
            "重新执行生产检查并附加 verify_runtime=true。",
        ),
        "event_bus": (
            "配置事件总线",
            "设置 Redis、Kafka 或 Redpanda 事件总线及其受控连接信息。",
            "GET /api/v1/system/dependencies 应报告事件总线连接成功。",
        ),
        "telemetry": (
            "启用遥测",
            "设置 RESEARCHFORGE_OTEL_ENABLED=1 和受控的 RESEARCHFORGE_OTEL_ENDPOINT。",
            "确认 Collector 收到 API 与 Worker Trace。",
        ),
        "queue_probe": (
            "验证队列连接",
            "检查 Redis 网络、凭据、TLS 和 Consumer Group 配置。",
            "GET /api/v1/system/dependencies 应报告 redis 为 ok。",
        ),
        "typed_repository_storage": (
            "完成仓库连接存储迁移",
            "使用 PostgreSQL 并让 API 启动完成 007_repository_connections_typed.sql 迁移。",
            "GET /api/v1/system/storage 应显示 typed_repository_connections=true。",
        ),
        "dependency_connectivity": (
            "修复外部依赖连通性",
            "逐项检查 PostgreSQL、Redis、对象存储、图数据库和事件总线的网络与运行身份。",
            "重新执行生产检查并附加 verify_dependencies=true。",
        ),
    }
    actions = []
    for check in checks:
        if check.get("passed"):
            continue
        check_id = str(check.get("id") or "")
        item = guidance.get(check_id)
        if item is None:
            continue
        title, configuration, validation = item
        actions.append({
            "check_id": check_id,
            "title": title,
            "configuration": configuration,
            "validation": validation,
        })
    return actions


def _redact_connection_string(value: str | None) -> str | None:
    if not value:
        return value
    parts = urlsplit(value)
    if not parts.scheme:
        return "***configured***"
    netloc = parts.hostname or ""
    if parts.port:
        netloc += f":{parts.port}"
    query = [
        (key, "***redacted***" if any(marker in key.lower() for marker in ("key", "token", "secret", "password")) else item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit(
        (
            parts.scheme,
            netloc,
            parts.path,
            urlencode(query),
            parts.fragment,
        )
    )


def _can_route_model() -> bool:
    try:
        store.select_model_config("coding", strict=True)
    except LookupError:
        return False
    return True


def _next_recommended_action(checks: list[dict[str, object]], no_evaluation_history: bool) -> str:
    failed_ids = [str(item["id"]) for item in checks if not item.get("passed")]
    if no_evaluation_history or "evaluation_report" in failed_ids:
        return "运行完整 10 个 Golden Task 的评测并生成验收报告。"
    if "sandbox_ready" in failed_ids:
        return "检查 Docker 沙箱或本地执行环境是否可用。"
    if "job_queue" in failed_ids:
        return "检查 Redis 连接、队列名称，或启动独立 Worker。"
    if "model_catalog" in failed_ids or "model_route" in failed_ids:
        return "补齐可用模型配置，或导入真实模型网关环境变量。"
    if "label_trace" in failed_ids or "export_dataset" in failed_ids:
        return "先跑几次 Agent Run 并生成可标注的 Trace 候选。"
    return "查看最近评测报告，确认失败样本和数据集版本是否需要审核。"


@router.get("/workspaces/{workspace_id}/quality")
async def workspace_quality_report(request: Request, workspace_id: str, days: int = Query(default=30, ge=1, le=365)):
    from app.api.context import require_workspace_access
    from app.services.operations import workspace_quality
    require_workspace_access(request, workspace_id)
    if workspace_id not in store.workspaces:
        raise HTTPException(404, "WORKSPACE_NOT_FOUND")
    return workspace_quality(store, workspace_id, days)


@router.get("/workspaces/{workspace_id}/retention")
async def preview_artifact_retention(request: Request, workspace_id: str, days: int = Query(default=30, ge=7, le=3650), limit: int = Query(default=500, ge=1, le=500)):
    from app.api.context import is_admin, require_workspace_access
    from app.services.operations import retention_preview
    if not is_admin(request): raise HTTPException(403, "ADMIN_REQUIRED")
    require_workspace_access(request, workspace_id)
    if workspace_id not in store.workspaces:
        raise HTTPException(404, "WORKSPACE_NOT_FOUND")
    return retention_preview(store, workspace_id, days, limit)


@router.post("/workspaces/{workspace_id}/retention")
async def apply_artifact_retention(request: Request, workspace_id: str, confirmation_digest: str = Query(min_length=64, max_length=64), days: int = Query(default=30, ge=7, le=3650), limit: int = Query(default=500, ge=1, le=500)):
    from app.api.context import is_admin, current_user, require_workspace_access
    from app.services.operations import retention_preview, expire_logs
    if not is_admin(request): raise HTTPException(403, "ADMIN_REQUIRED")
    require_workspace_access(request, workspace_id)
    if workspace_id not in store.workspaces:
        raise HTTPException(404, "WORKSPACE_NOT_FOUND")
    plan = retention_preview(store, workspace_id, days, limit)
    if plan["confirmation_digest"] != confirmation_digest:
        raise HTTPException(409, "RETENTION_PREVIEW_CHANGED")
    return expire_logs(store, plan, current_user(request).id)
