import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


def _billing_fx_rates() -> dict[str, float]:
    raw = os.getenv("RESEARCHFORGE_BILLING_FX_RATES", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("RESEARCHFORGE_BILLING_FX_RATES must be a JSON object") from exc
    if not isinstance(data, dict):
        raise ValueError("RESEARCHFORGE_BILLING_FX_RATES must be a JSON object")
    rates = {str(currency).upper(): float(rate) for currency, rate in data.items()}
    if any(len(currency) != 3 or rate <= 0 for currency, rate in rates.items()):
        raise ValueError("RESEARCHFORGE_BILLING_FX_RATES contains an invalid currency or rate")
    return rates


def _string_mapping_env(name: str) -> dict[str, str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be a JSON object") from exc
    if not isinstance(data, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in data.items()):
        raise ValueError(f"{name} must be a JSON object with string keys and values")
    return dict(data)


def _string_list_mapping_env(name: str) -> dict[str, list[str]]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be a JSON object") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{name} must be a JSON object of string arrays")
    normalized: dict[str, list[str]] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, list) or not value:
            raise ValueError(f"{name} must be a JSON object of non-empty string arrays")
        if any(not isinstance(item, str) or not item for item in value):
            raise ValueError(f"{name} must be a JSON object of non-empty string arrays")
        normalized[key.strip().lower().replace("-", "_")] = list(value)
    return normalized


def _object_list_env(name: str) -> list[dict[str, object]]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be a JSON array") from exc
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise ValueError(f"{name} must be a JSON array of objects")
    return [dict(item) for item in data]


class Settings(BaseModel):
    app_name: str = "ResearchForge Agent Harness"
    environment: str = "local"
    api_host: str = "127.0.0.1"
    api_port: int = 8001
    api_key: str | None = None
    api_key_env: str | None = None
    auth_mode: str = "auto"
    cors_origins: list[str] = ["http://localhost:3010", "http://127.0.0.1:3010", "http://localhost:3000", "http://127.0.0.1:3000"]
    allow_mock_models: bool = True
    rate_limit_per_minute: int = 600
    auth_rate_limit_per_minute: int = 30
    rate_limit_backend: str = "local"
    max_request_body_bytes: int = 5_000_000
    strict_benchmarks: bool = False
    kafka_bootstrap_servers: str = "127.0.0.1:9092"
    kafka_topic: str = "researchforge.events"
    otel_enabled: bool = False
    otel_endpoint: str = "http://127.0.0.1:4318/v1/traces"
    knowledge_backend: str = "local"
    embedding_base_url: str | None = None
    embedding_model: str | None = None
    embedding_api_key_env: str = "OPENAI_API_KEY"
    neo4j_uri: str | None = None
    neo4j_user: str = "neo4j"
    neo4j_password_env: str = "RESEARCHFORGE_NEO4J_PASSWORD"
    store_backend: str = "json"
    store_path: str
    repository_cache_root: str | None = None
    postgres_dsn: str | None = None
    postgres_pool_size: int = 10
    postgres_max_overflow: int = 20
    postgres_pool_timeout_seconds: int = 30
    postgres_pool_recycle_seconds: int = 1800
    artifact_store_backend: str = "filesystem"
    artifact_store_path: str = str(Path.cwd() / ".data" / "artifacts")
    artifact_store_bucket: str | None = None
    artifact_store_prefix: str = "artifacts"
    artifact_store_endpoint_url: str | None = None
    artifact_store_region: str = "us-east-1"
    artifact_store_access_key_id_env: str = "MINIO_ROOT_USER"
    artifact_store_secret_access_key_env: str = "MINIO_ROOT_PASSWORD"
    artifact_store_use_ssl: bool = False
    artifact_store_auto_create_bucket: bool = False
    artifact_store_server_side_encryption: str | None = None
    artifact_store_kms_key_id: str | None = None
    artifact_store_workspace_prefix: bool = True
    metrics_token_env: str = "RESEARCHFORGE_METRICS_TOKEN"
    sandbox_backend: str = "local"
    sandbox_image: str = "python:3.12-slim"
    sandbox_output_limit_bytes: int = 64_000
    sandbox_memory_limit: str = "512m"
    sandbox_cpu_limit: float = 1.0
    sandbox_pids_limit: int = 256
    sandbox_tmpfs_size: str = "64m"
    sandbox_user: str | None = None
    sandbox_kubernetes_namespace: str = "default"
    sandbox_kubernetes_kubeconfig: str | None = None
    sandbox_kubernetes_service_account: str | None = None
    sandbox_kubernetes_workspace_pvc: str | None = None
    sandbox_kubernetes_image_pull_policy: str = "IfNotPresent"
    sandbox_kubernetes_runtime_class: str | None = None
    sandbox_kubernetes_egress_rules: list[dict[str, object]] = []
    sandbox_kubernetes_active_deadline_seconds: int = 600
    sandbox_kubernetes_keep_pods: bool = False
    sandbox_shared_workspace_root: str | None = None
    sandbox_mount_mode: str = "rw"
    sandbox_max_concurrent: int = 8
    sandbox_queue_timeout_seconds: int = 30
    sandbox_prewarm_pull: bool = False
    sandbox_pool_reconcile_seconds: int = 300
    persistence_enabled: bool = True
    network_enabled: bool = False
    sandbox_network_enabled: bool = False
    sandbox_network_name: str | None = None
    # Reasonable for hosted coding models whose first token may take longer
    # than a short API probe. Individual deployments can still lower this.
    model_gateway_timeout_seconds: int = 45
    model_gateway_max_retries: int = 2
    model_gateway_retry_backoff_seconds: float = 0.2
    model_gateway_base_url: str | None = None
    model_gateway_provider: str = "openai_compatible"
    model_gateway_api_key_env: str = "OPENAI_API_KEY"
    model_gateway_default_model: str | None = None
    model_gateway_research_model: str | None = None
    model_gateway_catalog: list[dict[str, object]] = []
    model_gateway_context_window: int = 128_000
    model_gateway_cost_per_1k_tokens: float = 0.002
    agent_backend: str = "native"
    agent_command: str | None = None
    agent_commands: dict[str, list[str]] = {}
    agent_bridge_url: str | None = None
    agent_bridge_token_env: str = "RESEARCHFORGE_AGENT_BRIDGE_TOKEN"
    agent_model_mode: str = "gateway"
    agent_timeout_seconds: int = 900
    agent_output_limit_bytes: int = 128_000
    agent_max_delegation_depth: int = 2
    billing_fx_rates: dict[str, float] = {}
    research_provider: str = "local"
    research_crossref_mailto: str | None = None
    research_timeout_seconds: int = 8
    job_queue_backend: str = "local_background"
    redis_url: str = "redis://127.0.0.1:6379/0"
    job_queue_name: str = "researchforge:jobs"
    job_worker_poll_seconds: float = 1.0
    job_worker_enabled: bool = True
    job_max_retries: int = 2
    job_workspace_concurrency: int = 1
    job_queue_wait_timeout_seconds: int = 3600
    job_delivery_mode: str = "stream"
    # Independent heartbeats renew long-running deliveries. A two-minute lease
    # bounds crash recovery without exceeding the default repair time budget.
    job_visibility_timeout_seconds: int = 120
    workflow_backend: str = "queue"
    temporal_address: str = "127.0.0.1:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "researchforge-workflows"
    training_controller_enabled: bool = True
    training_concurrency: int = 1
    training_poll_seconds: float = 10.0
    training_backend: str = "docker"
    training_image: str = "researchforge-trainer:local"
    training_memory_limit: str = "8g"
    training_cpu_limit: str = "2"
    training_gpus: str | None = None
    training_kubernetes_namespace: str = "default"
    training_kubernetes_kubeconfig: str | None = None
    training_kubernetes_service_account: str | None = None
    training_kubernetes_workspace_pvc: str | None = None
    training_kubernetes_shared_root: str | None = None
    training_kubernetes_image_pull_policy: str = "IfNotPresent"
    training_kubernetes_keep_jobs: bool = False
    training_kubernetes_node_selector: dict[str, str] = {}
    training_kubernetes_tolerations: list[dict[str, object]] = []
    event_bus_backend: str = "none"
    event_bus_url: str | None = None
    event_bus_channel: str = "researchforge:events"
    event_bus_publish_timeout_seconds: float = 5.0
    event_bus_max_retries: int = 2
    event_bus_retry_backoff_seconds: float = 0.25
    event_bus_circuit_breaker_seconds: int = 30
    github_oauth_client_id: str | None = None
    github_oauth_client_secret_env: str = "RESEARCHFORGE_GITHUB_CLIENT_SECRET"
    github_oauth_redirect_uri: str = "http://127.0.0.1:3010/"
    github_oauth_authorize_url: str = "https://github.com/login/oauth/authorize"
    github_oauth_token_url: str = "https://github.com/login/oauth/access_token"
    github_oauth_scopes: str = "read:user user:email"
    github_oauth_state_secret_env: str = "RESEARCHFORGE_GITHUB_OAUTH_STATE_SECRET"
    github_oauth_state_ttl_seconds: int = 600
    github_api_base_url: str = "https://api.github.com"
    github_git_transport: Literal["git", "api"] = "git"
    github_app_id: str | None = None
    github_app_private_key_env: str = "RESEARCHFORGE_GITHUB_APP_PRIVATE_KEY"
    github_app_token_refresh_seconds: int = 300
    github_sync_max_retries: int = 2
    github_sync_retry_backoff_seconds: float = 1.0
    github_sync_circuit_breaker_seconds: int = 30
    github_webhook_secret_env: str = "RESEARCHFORGE_GITHUB_WEBHOOK_SECRET"
    github_webhook_public_url: str | None = None
    github_issue_trigger_label: str = "researchforge"
    scim_bearer_token_env: str = "RESEARCHFORGE_SCIM_BEARER_TOKEN"
    scim_default_workspace_id: str = "workspace_default"
    scim_allowed_workspace_ids: list[str] = ["workspace_default"]
    scim_groups_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings(
        environment=os.getenv("RESEARCHFORGE_ENV", "local"),
        api_host=os.getenv("RESEARCHFORGE_API_HOST", "127.0.0.1"),
        api_port=int(os.getenv("RESEARCHFORGE_API_PORT", "8001")),
        api_key=os.getenv("RESEARCHFORGE_API_KEY") or None,
        api_key_env=os.getenv("RESEARCHFORGE_API_KEY_ENV") or None,
        auth_mode=os.getenv("RESEARCHFORGE_AUTH_MODE", "auto"),
        cors_origins=[value.strip() for value in os.getenv("RESEARCHFORGE_CORS_ORIGINS", "http://localhost:3010,http://127.0.0.1:3010,http://localhost:3000,http://127.0.0.1:3000").split(",") if value.strip()],
        allow_mock_models=os.getenv("RESEARCHFORGE_ALLOW_MOCK_MODELS", "0" if os.getenv("RESEARCHFORGE_ENV") in {"production", "prod"} else "1") == "1",
        rate_limit_per_minute=int(os.getenv("RESEARCHFORGE_RATE_LIMIT_PER_MINUTE", "120" if os.getenv("RESEARCHFORGE_ENV") in {"production", "prod"} else "600")),
        auth_rate_limit_per_minute=int(os.getenv("RESEARCHFORGE_AUTH_RATE_LIMIT_PER_MINUTE", "30")),
        rate_limit_backend=os.getenv("RESEARCHFORGE_RATE_LIMIT_BACKEND", "redis" if os.getenv("RESEARCHFORGE_ENV") in {"production", "prod"} else "local"),
        max_request_body_bytes=int(os.getenv("RESEARCHFORGE_MAX_REQUEST_BODY_BYTES", "5000000")),
        strict_benchmarks=os.getenv("RESEARCHFORGE_STRICT_BENCHMARKS", "1" if os.getenv("RESEARCHFORGE_ENV") in {"production", "prod"} else "0") == "1",
        kafka_bootstrap_servers=os.getenv("RESEARCHFORGE_KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092"),
        kafka_topic=os.getenv("RESEARCHFORGE_KAFKA_TOPIC", "researchforge.events"),
        otel_enabled=os.getenv("RESEARCHFORGE_OTEL_ENABLED", "0") == "1",
        otel_endpoint=os.getenv("RESEARCHFORGE_OTEL_ENDPOINT", "http://127.0.0.1:4318/v1/traces"),
        knowledge_backend=os.getenv("RESEARCHFORGE_KNOWLEDGE_BACKEND", "local"),
        embedding_base_url=os.getenv("RESEARCHFORGE_EMBEDDING_BASE_URL"),
        embedding_model=os.getenv("RESEARCHFORGE_EMBEDDING_MODEL"),
        embedding_api_key_env=os.getenv("RESEARCHFORGE_EMBEDDING_API_KEY_ENV", "OPENAI_API_KEY"),
        neo4j_uri=os.getenv("RESEARCHFORGE_NEO4J_URI"),
        neo4j_user=os.getenv("RESEARCHFORGE_NEO4J_USER", "neo4j"),
        neo4j_password_env=os.getenv("RESEARCHFORGE_NEO4J_PASSWORD_ENV", "RESEARCHFORGE_NEO4J_PASSWORD"),
        store_backend=os.getenv("RESEARCHFORGE_STORE_BACKEND", "json"),
        store_path=os.getenv("RESEARCHFORGE_STORE_PATH", str(Path.cwd() / ".data" / "store.json")),
        repository_cache_root=os.getenv("RESEARCHFORGE_REPOSITORY_CACHE_ROOT") or None,
        postgres_dsn=os.getenv("RESEARCHFORGE_POSTGRES_DSN"),
        postgres_pool_size=max(1, min(100, int(os.getenv("RESEARCHFORGE_POSTGRES_POOL_SIZE", "10")))),
        postgres_max_overflow=max(0, min(200, int(os.getenv("RESEARCHFORGE_POSTGRES_MAX_OVERFLOW", "20")))),
        postgres_pool_timeout_seconds=max(1, min(300, int(os.getenv("RESEARCHFORGE_POSTGRES_POOL_TIMEOUT_SECONDS", "30")))),
        postgres_pool_recycle_seconds=max(60, min(86400, int(os.getenv("RESEARCHFORGE_POSTGRES_POOL_RECYCLE_SECONDS", "1800")))),
        artifact_store_backend=os.getenv("RESEARCHFORGE_ARTIFACT_STORE_BACKEND", "filesystem"),
        artifact_store_path=os.getenv(
            "RESEARCHFORGE_ARTIFACT_STORE_PATH",
            str(Path.cwd() / ".data" / "artifacts"),
        ),
        artifact_store_bucket=os.getenv("RESEARCHFORGE_ARTIFACT_STORE_BUCKET") or None,
        artifact_store_prefix=os.getenv("RESEARCHFORGE_ARTIFACT_STORE_PREFIX", "artifacts"),
        artifact_store_endpoint_url=os.getenv("RESEARCHFORGE_ARTIFACT_STORE_ENDPOINT_URL") or None,
        artifact_store_region=os.getenv("RESEARCHFORGE_ARTIFACT_STORE_REGION", "us-east-1"),
        artifact_store_access_key_id_env=os.getenv(
            "RESEARCHFORGE_ARTIFACT_STORE_ACCESS_KEY_ID_ENV",
            "MINIO_ROOT_USER",
        ),
        artifact_store_secret_access_key_env=os.getenv(
            "RESEARCHFORGE_ARTIFACT_STORE_SECRET_ACCESS_KEY_ENV",
            "MINIO_ROOT_PASSWORD",
        ),
        artifact_store_use_ssl=os.getenv("RESEARCHFORGE_ARTIFACT_STORE_USE_SSL", "0") == "1",
        artifact_store_auto_create_bucket=os.getenv(
            "RESEARCHFORGE_ARTIFACT_STORE_AUTO_CREATE_BUCKET", "0"
        ) == "1",
        artifact_store_server_side_encryption=os.getenv(
            "RESEARCHFORGE_ARTIFACT_STORE_SERVER_SIDE_ENCRYPTION"
        ) or None,
        artifact_store_kms_key_id=os.getenv("RESEARCHFORGE_ARTIFACT_STORE_KMS_KEY_ID") or None,
        artifact_store_workspace_prefix=os.getenv(
            "RESEARCHFORGE_ARTIFACT_STORE_WORKSPACE_PREFIX", "1"
        ) == "1",
        metrics_token_env=os.getenv(
            "RESEARCHFORGE_METRICS_TOKEN_ENV", "RESEARCHFORGE_METRICS_TOKEN"
        ),
        sandbox_backend=os.getenv("RESEARCHFORGE_SANDBOX_BACKEND", "local"),
        sandbox_image=os.getenv("RESEARCHFORGE_SANDBOX_IMAGE", "python:3.12-slim"),
        sandbox_output_limit_bytes=int(os.getenv("RESEARCHFORGE_SANDBOX_OUTPUT_LIMIT_BYTES", "64000")),
        sandbox_memory_limit=os.getenv("RESEARCHFORGE_SANDBOX_MEMORY_LIMIT", "512m"),
        sandbox_cpu_limit=float(os.getenv("RESEARCHFORGE_SANDBOX_CPU_LIMIT", "1.0")),
        sandbox_pids_limit=int(os.getenv("RESEARCHFORGE_SANDBOX_PIDS_LIMIT", "256")),
        sandbox_tmpfs_size=os.getenv("RESEARCHFORGE_SANDBOX_TMPFS_SIZE", "64m"),
        sandbox_user=os.getenv("RESEARCHFORGE_SANDBOX_USER") or None,
        sandbox_kubernetes_namespace=os.getenv("RESEARCHFORGE_SANDBOX_KUBERNETES_NAMESPACE", "default"),
        sandbox_kubernetes_kubeconfig=os.getenv("RESEARCHFORGE_SANDBOX_KUBECONFIG") or None,
        sandbox_kubernetes_service_account=os.getenv("RESEARCHFORGE_SANDBOX_KUBERNETES_SERVICE_ACCOUNT") or None,
        sandbox_kubernetes_workspace_pvc=os.getenv("RESEARCHFORGE_SANDBOX_KUBERNETES_WORKSPACE_PVC") or None,
        sandbox_kubernetes_image_pull_policy=os.getenv("RESEARCHFORGE_SANDBOX_KUBERNETES_IMAGE_PULL_POLICY", "IfNotPresent"),
        sandbox_kubernetes_runtime_class=os.getenv("RESEARCHFORGE_SANDBOX_KUBERNETES_RUNTIME_CLASS") or None,
        sandbox_kubernetes_egress_rules=_object_list_env(
            "RESEARCHFORGE_SANDBOX_KUBERNETES_EGRESS_RULES"
        ),
        sandbox_kubernetes_active_deadline_seconds=int(os.getenv("RESEARCHFORGE_SANDBOX_KUBERNETES_ACTIVE_DEADLINE_SECONDS", "600")),
        sandbox_kubernetes_keep_pods=os.getenv("RESEARCHFORGE_SANDBOX_KUBERNETES_KEEP_PODS", "0") == "1",
        sandbox_shared_workspace_root=os.getenv("RESEARCHFORGE_SANDBOX_SHARED_WORKSPACE_ROOT") or None,
        sandbox_mount_mode=os.getenv("RESEARCHFORGE_SANDBOX_MOUNT_MODE", "rw"),
        sandbox_max_concurrent=max(1, min(1024, int(os.getenv("RESEARCHFORGE_SANDBOX_MAX_CONCURRENT", "8")))),
        sandbox_queue_timeout_seconds=max(0, min(300, int(os.getenv("RESEARCHFORGE_SANDBOX_QUEUE_TIMEOUT_SECONDS", "30")))),
        sandbox_prewarm_pull=os.getenv("RESEARCHFORGE_SANDBOX_PREWARM_PULL", "0") == "1",
        sandbox_pool_reconcile_seconds=max(10, min(3600, int(os.getenv("RESEARCHFORGE_SANDBOX_POOL_RECONCILE_SECONDS", "300")))),
        persistence_enabled=os.getenv("RESEARCHFORGE_PERSISTENCE", "1") != "0",
        network_enabled=os.getenv("RESEARCHFORGE_NETWORK_ENABLED", "0") == "1",
        sandbox_network_enabled=os.getenv("RESEARCHFORGE_SANDBOX_NETWORK_ENABLED", "0" if os.getenv("RESEARCHFORGE_ENV") in {"production", "prod"} else os.getenv("RESEARCHFORGE_NETWORK_ENABLED", "0")) == "1",
        sandbox_network_name=os.getenv("RESEARCHFORGE_SANDBOX_NETWORK_NAME") or None,
        model_gateway_timeout_seconds=int(os.getenv("RESEARCHFORGE_MODEL_TIMEOUT_SECONDS", "45")),
        model_gateway_max_retries=int(os.getenv("RESEARCHFORGE_MODEL_MAX_RETRIES", "2")),
        model_gateway_retry_backoff_seconds=float(
            os.getenv("RESEARCHFORGE_MODEL_RETRY_BACKOFF_SECONDS", "0.2")
        ),
        model_gateway_base_url=os.getenv("RESEARCHFORGE_MODEL_BASE_URL"),
        model_gateway_provider=os.getenv("RESEARCHFORGE_MODEL_PROVIDER", "openai_compatible"),
        model_gateway_api_key_env=os.getenv("RESEARCHFORGE_MODEL_API_KEY_ENV", "OPENAI_API_KEY"),
        model_gateway_default_model=os.getenv("RESEARCHFORGE_MODEL_NAME"),
        model_gateway_research_model=os.getenv("RESEARCHFORGE_RESEARCH_MODEL_NAME"),
        model_gateway_catalog=_object_list_env("RESEARCHFORGE_MODEL_CATALOG"),
        model_gateway_context_window=int(os.getenv("RESEARCHFORGE_MODEL_CONTEXT_WINDOW", "128000")),
        model_gateway_cost_per_1k_tokens=float(os.getenv("RESEARCHFORGE_MODEL_COST_PER_1K_TOKENS", "0.002")),
        agent_backend=os.getenv("RESEARCHFORGE_AGENT_BACKEND", "native").strip().lower() or "native",
        agent_command=os.getenv("RESEARCHFORGE_AGENT_COMMAND") or None,
        agent_commands=_string_list_mapping_env("RESEARCHFORGE_AGENT_COMMANDS"),
        agent_bridge_url=os.getenv("RESEARCHFORGE_AGENT_BRIDGE_URL") or None,
        agent_bridge_token_env=os.getenv("RESEARCHFORGE_AGENT_BRIDGE_TOKEN_ENV", "RESEARCHFORGE_AGENT_BRIDGE_TOKEN"),
        agent_model_mode=os.getenv("RESEARCHFORGE_AGENT_MODEL_MODE", "gateway").strip().lower() or "gateway",
        agent_timeout_seconds=max(30, min(7200, int(os.getenv("RESEARCHFORGE_AGENT_TIMEOUT_SECONDS", "900")))),
        agent_output_limit_bytes=max(4096, min(2_000_000, int(os.getenv("RESEARCHFORGE_AGENT_OUTPUT_LIMIT_BYTES", "128000")))),
        agent_max_delegation_depth=max(1, min(8, int(os.getenv("RESEARCHFORGE_AGENT_MAX_DELEGATION_DEPTH", "2")))),
        billing_fx_rates=_billing_fx_rates(),
        research_provider=os.getenv("RESEARCHFORGE_RESEARCH_PROVIDER", "local"),
        research_crossref_mailto=os.getenv("RESEARCHFORGE_CROSSREF_MAILTO"),
        research_timeout_seconds=int(os.getenv("RESEARCHFORGE_RESEARCH_TIMEOUT_SECONDS", "8")),
        job_queue_backend=os.getenv("RESEARCHFORGE_JOB_QUEUE_BACKEND", "local_background"),
        redis_url=os.getenv("RESEARCHFORGE_REDIS_URL", "redis://127.0.0.1:6379/0"),
        job_queue_name=os.getenv("RESEARCHFORGE_JOB_QUEUE_NAME", "researchforge:jobs"),
        job_worker_poll_seconds=float(os.getenv("RESEARCHFORGE_JOB_WORKER_POLL_SECONDS", "1.0")),
        job_worker_enabled=os.getenv("RESEARCHFORGE_JOB_WORKER_ENABLED", "1") != "0",
        job_max_retries=int(os.getenv("RESEARCHFORGE_JOB_MAX_RETRIES", "2")),
        job_workspace_concurrency=max(1, min(64, int(os.getenv("RESEARCHFORGE_JOB_WORKSPACE_CONCURRENCY", "1")))),
        job_queue_wait_timeout_seconds=max(30, int(os.getenv("RESEARCHFORGE_JOB_QUEUE_WAIT_TIMEOUT_SECONDS", "3600"))),
        job_delivery_mode=os.getenv("RESEARCHFORGE_JOB_DELIVERY_MODE", "stream"),
        job_visibility_timeout_seconds=int(os.getenv("RESEARCHFORGE_JOB_VISIBILITY_TIMEOUT_SECONDS", "120")),
        workflow_backend=os.getenv("RESEARCHFORGE_WORKFLOW_BACKEND", "queue").strip().lower() or "queue",
        temporal_address=os.getenv("RESEARCHFORGE_TEMPORAL_ADDRESS", "127.0.0.1:7233").strip(),
        temporal_namespace=os.getenv("RESEARCHFORGE_TEMPORAL_NAMESPACE", "default").strip() or "default",
        temporal_task_queue=os.getenv("RESEARCHFORGE_TEMPORAL_TASK_QUEUE", "researchforge-workflows").strip() or "researchforge-workflows",
        training_controller_enabled=os.getenv("RESEARCHFORGE_TRAINING_CONTROLLER_ENABLED", "1") == "1",
        training_concurrency=max(1, min(64, int(os.getenv("RESEARCHFORGE_TRAINING_MAX_CONCURRENT", os.getenv("RESEARCHFORGE_TRAINING_CONCURRENCY", "1"))))),
        training_poll_seconds=max(1.0, float(os.getenv("RESEARCHFORGE_TRAINING_POLL_SECONDS", "10"))),
        training_backend=os.getenv("RESEARCHFORGE_TRAINING_BACKEND", "docker").strip().lower(),
        training_image=os.getenv("RESEARCHFORGE_TRAINING_IMAGE", "researchforge-trainer:local"),
        training_memory_limit=os.getenv("RESEARCHFORGE_TRAINING_MEMORY", "8g"),
        training_cpu_limit=os.getenv("RESEARCHFORGE_TRAINING_CPUS", "2"),
        training_gpus=os.getenv("RESEARCHFORGE_TRAINING_GPUS") or None,
        training_kubernetes_namespace=os.getenv(
            "RESEARCHFORGE_TRAINING_KUBERNETES_NAMESPACE",
            os.getenv("RESEARCHFORGE_SANDBOX_KUBERNETES_NAMESPACE", "default"),
        ),
        training_kubernetes_kubeconfig=os.getenv("RESEARCHFORGE_TRAINING_KUBECONFIG") or os.getenv("RESEARCHFORGE_SANDBOX_KUBECONFIG") or None,
        training_kubernetes_service_account=os.getenv("RESEARCHFORGE_TRAINING_KUBERNETES_SERVICE_ACCOUNT") or os.getenv("RESEARCHFORGE_SANDBOX_KUBERNETES_SERVICE_ACCOUNT") or None,
        training_kubernetes_workspace_pvc=os.getenv("RESEARCHFORGE_TRAINING_KUBERNETES_WORKSPACE_PVC") or os.getenv("RESEARCHFORGE_SANDBOX_KUBERNETES_WORKSPACE_PVC") or None,
        training_kubernetes_shared_root=os.getenv("RESEARCHFORGE_TRAINING_KUBERNETES_SHARED_ROOT") or os.getenv("RESEARCHFORGE_SANDBOX_SHARED_WORKSPACE_ROOT") or None,
        training_kubernetes_image_pull_policy=os.getenv("RESEARCHFORGE_TRAINING_KUBERNETES_IMAGE_PULL_POLICY", "IfNotPresent"),
        training_kubernetes_keep_jobs=os.getenv("RESEARCHFORGE_TRAINING_KUBERNETES_KEEP_JOBS", "0") == "1",
        training_kubernetes_node_selector=_string_mapping_env("RESEARCHFORGE_TRAINING_KUBERNETES_NODE_SELECTOR"),
        training_kubernetes_tolerations=_object_list_env("RESEARCHFORGE_TRAINING_KUBERNETES_TOLERATIONS"),
        event_bus_backend=os.getenv("RESEARCHFORGE_EVENT_BUS_BACKEND", "none"),
        event_bus_url=os.getenv("RESEARCHFORGE_EVENT_BUS_URL") or None,
        event_bus_channel=os.getenv("RESEARCHFORGE_EVENT_BUS_CHANNEL", "researchforge:events"),
        event_bus_publish_timeout_seconds=max(0.1, min(60.0, float(os.getenv("RESEARCHFORGE_EVENT_BUS_PUBLISH_TIMEOUT_SECONDS", "5")))),
        event_bus_max_retries=max(0, min(10, int(os.getenv("RESEARCHFORGE_EVENT_BUS_MAX_RETRIES", "2")))),
        event_bus_retry_backoff_seconds=max(0.0, min(30.0, float(os.getenv("RESEARCHFORGE_EVENT_BUS_RETRY_BACKOFF_SECONDS", "0.25")))),
        event_bus_circuit_breaker_seconds=max(1, min(3600, int(os.getenv("RESEARCHFORGE_EVENT_BUS_CIRCUIT_BREAKER_SECONDS", "30")))),
        github_oauth_client_id=os.getenv("RESEARCHFORGE_GITHUB_CLIENT_ID") or None,
        github_oauth_client_secret_env=os.getenv(
            "RESEARCHFORGE_GITHUB_CLIENT_SECRET_ENV",
            "RESEARCHFORGE_GITHUB_CLIENT_SECRET",
        ),
        github_oauth_redirect_uri=os.getenv(
            "RESEARCHFORGE_GITHUB_REDIRECT_URI",
            "http://127.0.0.1:3010/",
        ),
        github_oauth_authorize_url=os.getenv(
            "RESEARCHFORGE_GITHUB_AUTHORIZE_URL",
            "https://github.com/login/oauth/authorize",
        ),
        github_oauth_token_url=os.getenv(
            "RESEARCHFORGE_GITHUB_TOKEN_URL",
            "https://github.com/login/oauth/access_token",
        ),
        github_oauth_scopes=os.getenv(
            "RESEARCHFORGE_GITHUB_SCOPES",
            "read:user user:email",
        ),
        github_oauth_state_secret_env=os.getenv(
            "RESEARCHFORGE_GITHUB_STATE_SECRET_ENV",
            "RESEARCHFORGE_GITHUB_OAUTH_STATE_SECRET",
        ),
        github_oauth_state_ttl_seconds=int(
            os.getenv("RESEARCHFORGE_GITHUB_STATE_TTL_SECONDS", "600")
        ),
        github_api_base_url=os.getenv(
            "RESEARCHFORGE_GITHUB_API_BASE_URL",
            "https://api.github.com",
        ),
        github_app_id=os.getenv("RESEARCHFORGE_GITHUB_APP_ID") or None,
        github_git_transport=os.getenv("RESEARCHFORGE_GITHUB_GIT_TRANSPORT", "git"),
        github_app_private_key_env=os.getenv(
            "RESEARCHFORGE_GITHUB_APP_PRIVATE_KEY_ENV",
            "RESEARCHFORGE_GITHUB_APP_PRIVATE_KEY",
        ),
        github_app_token_refresh_seconds=max(30, min(3600, int(os.getenv("RESEARCHFORGE_GITHUB_APP_TOKEN_REFRESH_SECONDS", "300")))),
        github_sync_max_retries=max(0, min(10, int(os.getenv("RESEARCHFORGE_GITHUB_SYNC_MAX_RETRIES", "2")))),
        github_sync_retry_backoff_seconds=max(0.0, min(60.0, float(os.getenv("RESEARCHFORGE_GITHUB_SYNC_RETRY_BACKOFF_SECONDS", "1")))),
        github_sync_circuit_breaker_seconds=max(1, min(3600, int(os.getenv("RESEARCHFORGE_GITHUB_SYNC_CIRCUIT_BREAKER_SECONDS", "30")))),
        github_webhook_secret_env=os.getenv(
            "RESEARCHFORGE_GITHUB_WEBHOOK_SECRET_ENV",
            "RESEARCHFORGE_GITHUB_WEBHOOK_SECRET",
        ),
        github_webhook_public_url=os.getenv("RESEARCHFORGE_GITHUB_WEBHOOK_PUBLIC_URL") or None,
        github_issue_trigger_label=(
            os.getenv("RESEARCHFORGE_GITHUB_ISSUE_TRIGGER_LABEL", "researchforge").strip()
            or "researchforge"
        ),
        scim_bearer_token_env=os.getenv("RESEARCHFORGE_SCIM_BEARER_TOKEN_ENV", "RESEARCHFORGE_SCIM_BEARER_TOKEN"),
        scim_default_workspace_id=os.getenv("RESEARCHFORGE_SCIM_DEFAULT_WORKSPACE_ID", "workspace_default"),
        scim_allowed_workspace_ids=[value.strip() for value in os.getenv("RESEARCHFORGE_SCIM_ALLOWED_WORKSPACE_IDS", os.getenv("RESEARCHFORGE_SCIM_DEFAULT_WORKSPACE_ID", "workspace_default")).split(",") if value.strip()],
        scim_groups_enabled=os.getenv("RESEARCHFORGE_SCIM_GROUPS_ENABLED", "1") == "1",
    )

