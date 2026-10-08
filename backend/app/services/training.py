from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from pydantic import BaseModel, Field
from typing import Literal

from app.config import get_settings
from app.domain.schemas import RunStatus


class TrainingRequest(BaseModel):
    workspace_id: str = "workspace_default"
    method: Literal["sft", "dpo", "grpo"] = "sft"
    base_model: str = Field(min_length=1, max_length=200)
    item_ids: list[str] = Field(default_factory=list, max_length=10000)
    online_rl_session_id: str | None = Field(default=None, min_length=1, max_length=200)
    max_steps: int = Field(default=100, ge=1, le=10000)
    max_seconds: int = Field(default=3600, ge=60, le=86400)
    learning_rate: float = Field(default=0.00002, gt=0, le=0.01)
    gpu_count: int = Field(default=0, ge=0, le=8)
    world_size: int = Field(default=1, ge=1, le=8)
    node_count: int = Field(default=1, ge=1, le=32)


def now():
    return datetime.now(timezone.utc).isoformat()


def training_root() -> Path:
    return Path(os.getenv("RESEARCHFORGE_TRAINING_ROOT", ".data/training")).resolve()


def job_dir(job_id: str) -> Path:
    if not job_id.startswith("train_") or not job_id[6:].isalnum():
        raise ValueError("INVALID_TRAINING_ID")
    path = training_root() / job_id
    if path.is_symlink():
        raise ValueError("TRAINING_PATH_SYMLINK")
    return path


def _completion(store, run_id, workspace_id):
    run = store.get_run(run_id)
    task = store.get_task(run.task_id) if run else None
    if not task or task.workspace_id != workspace_id:
        raise ValueError("TRAINING_DATA_WORKSPACE_MISMATCH")
    artifacts = store.list_artifacts(run_id)
    patches = [item.content for item in artifacts if str(item.type) in {"diff", "patch"} and item.content]
    if not patches:
        raise ValueError("TRAINING_PATCH_REQUIRED")
    return task.id, task.goal, patches[-1]


def _online_rl_rows(store, request: TrainingRequest) -> tuple[list[dict], dict]:
    if request.method != "grpo":
        raise ValueError("ONLINE_RL_REQUIRES_GRPO")
    session = store.online_rl_sessions.get(request.online_rl_session_id or "")
    if not session or session.get("workspace_id") != request.workspace_id:
        raise ValueError("ONLINE_RL_SESSION_NOT_FOUND")
    exported = session.get("exported_dataset") or {}
    rollout_ids = exported.get("rollout_ids") or []
    if not session.get("training_ready") or session.get("status") != "frozen" or not rollout_ids:
        raise ValueError("ONLINE_RL_DATA_NOT_FROZEN")
    selected = {str(item_id) for item_id in rollout_ids}
    rows = []
    for rollout in session.get("rollouts") or []:
        if rollout.get("id") not in selected:
            continue
        prompt = rollout.get("prompt")
        reference_patch = rollout.get("reference_patch")
        if not isinstance(prompt, str) or not prompt or not isinstance(reference_patch, str) or not reference_patch:
            raise ValueError("ONLINE_RL_TRAINING_DATA_INVALID")
        rows.append({"prompt": prompt, "reference_patch": reference_patch})
    if len(rows) != len(selected):
        raise ValueError("ONLINE_RL_TRAINING_DATA_INVALID")
    return rows, exported


def prepare_training(store, request: TrainingRequest, actor_id: str) -> dict:
    models = json.loads(os.getenv("RESEARCHFORGE_TRAINING_MODELS", "{}"))
    if request.base_model not in models:
        raise ValueError("TRAINING_BASE_MODEL_NOT_ALLOWED")
    if len(request.item_ids) != len(set(request.item_ids)):
        raise ValueError("TRAINING_DUPLICATE_ITEMS")
    if request.online_rl_session_id and request.item_ids:
        raise ValueError("TRAINING_DATA_SOURCE_AMBIGUOUS")
    if not request.online_rl_session_id and not request.item_ids:
        raise ValueError("TRAINING_DATA_REQUIRED")
    rows: list[dict] = []
    online_rl_export: dict | None = None
    if request.online_rl_session_id:
        rows, online_rl_export = _online_rl_rows(store, request)
    for item_id in request.item_ids:
        if request.method == "sft":
            item = store.trace_dataset_items.get(item_id)
            if not item or item.status != "approved" or not item.usable_for_sft:
                raise ValueError("APPROVED_SFT_DATA_REQUIRED")
            _, prompt, completion = _completion(store, item.agent_run_id, request.workspace_id)
            rows.append({"prompt": prompt, "completion": completion})
        elif request.method == "dpo":
            pair = store.preference_pairs.get(item_id)
            if not pair or pair.status != "approved":
                raise ValueError("APPROVED_PREFERENCE_REQUIRED")
            task_id, prompt, chosen = _completion(store, pair.chosen_run_id, request.workspace_id)
            other_task, _, rejected = _completion(store, pair.rejected_run_id, request.workspace_id)
            if task_id != other_task or chosen == rejected:
                raise ValueError("PREFERENCE_REQUIRES_DISTINCT_COMPLETIONS_FOR_SAME_TASK")
            rows.append({"prompt": prompt, "chosen": chosen, "rejected": rejected})
        else:
            item = store.trace_dataset_items.get(item_id)
            if not item or item.status != "approved" or not item.usable_for_rl:
                raise ValueError("APPROVED_RL_DATA_REQUIRED")
            _, prompt, reference_patch = _completion(store, item.agent_run_id, request.workspace_id)
            rows.append({"prompt": prompt, "reference_patch": reference_patch})
    payload = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    if len(payload.encode()) > 50_000_000:
        raise ValueError("TRAINING_DATA_TOO_LARGE")
    job_id = "train_" + uuid4().hex
    path = job_dir(job_id)
    (path / "input").mkdir(parents=True)
    (path / "output").mkdir()
    (path / "input" / "dataset.jsonl").write_bytes(payload.encode("utf-8"))
    (path / "input" / "config.json").write_text(request.model_dump_json(), encoding="utf-8")
    record = {"id": job_id, **request.model_dump(), "status": "prepared", "dataset_sha256": hashlib.sha256(payload.encode()).hexdigest(),
              "sample_count": len(rows), "created_at": now(), "created_by": actor_id, "base_path": str(Path(models[request.base_model]).resolve())}
    if online_rl_export is not None:
        record["online_rl_export_sha256"] = online_rl_export["sha256"]
        record["online_rl_rollout_count"] = online_rl_export["row_count"]
    store.training_jobs[job_id] = record
    store.add_audit_log(action="training.prepare", resource_type="training_job", resource_id=job_id, actor_id=actor_id, decision="allow", detail_json={"online_rl_session_id": request.online_rl_session_id, "sample_count": len(rows)})
    store._persist()
    return record


def docker(*args, check=True):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
    if check and result.returncode:
        raise RuntimeError("TRAINING_CONTAINER_OPERATION_FAILED")
    return result


def training_backend() -> str:
    backend = get_settings().training_backend
    if backend == "k8s":
        backend = "kubernetes"
    if backend not in {"docker", "kubernetes"}:
        raise ValueError("TRAINING_BACKEND_UNSUPPORTED")
    return backend


def _record_backend(record: dict) -> str:
    backend = str(record.get("backend") or training_backend()).lower()
    if backend == "k8s":
        backend = "kubernetes"
    if backend not in {"docker", "kubernetes"}:
        raise ValueError("TRAINING_BACKEND_UNSUPPORTED")
    return backend


def training_job_name(record: dict) -> str:
    job_id = str(record.get("id") or "")
    if not job_id.startswith("train_") or not job_id[6:].isalnum():
        raise ValueError("INVALID_TRAINING_ID")
    return "rf-train-" + job_id[6:].lower()


def _kubectl_base_command() -> list[str]:
    settings = get_settings()
    command = ["kubectl"]
    if settings.training_kubernetes_kubeconfig:
        command.extend(["--kubeconfig", settings.training_kubernetes_kubeconfig])
    return command


def kubectl(*args, check=True):
    result = subprocess.run(
        [*_kubectl_base_command(), *args],
        capture_output=True,
        text=True,
        timeout=30,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode:
        raise RuntimeError("TRAINING_KUBERNETES_OPERATION_FAILED")
    return result


def _training_paths(record: dict) -> tuple[Path, Path]:
    path = job_dir(record["id"])
    if hashlib.sha256((path / "input/dataset.jsonl").read_bytes()).hexdigest() != record["dataset_sha256"]:
        raise ValueError("TRAINING_DATA_DIGEST_MISMATCH")
    model = Path(record["base_path"])
    if not model.is_dir() or not (model / "config.json").is_file():
        raise ValueError("TRAINING_LOCAL_MODEL_REQUIRED")
    return path, model


def _kubernetes_sub_path(path: Path) -> str:
    settings = get_settings()
    if not settings.training_kubernetes_workspace_pvc or not settings.training_kubernetes_shared_root:
        raise ValueError("TRAINING_KUBERNETES_SHARED_WORKSPACE_REQUIRED")
    shared_root = Path(settings.training_kubernetes_shared_root).expanduser().resolve()
    try:
        relative = path.resolve().relative_to(shared_root)
    except ValueError as exc:
        raise ValueError("TRAINING_KUBERNETES_PATH_NOT_SHARED") from exc
    if relative == Path("."):
        raise ValueError("TRAINING_KUBERNETES_PATH_INVALID")
    return relative.as_posix()


def _requested_gpu_count(record: dict) -> str | None:
    configured = int(record.get("gpu_count") or 0)
    return str(configured) if configured else get_settings().training_gpus


def _kubernetes_gpu_limit(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip()
    if not normalized.isdigit() or int(normalized) < 1:
        raise ValueError("TRAINING_KUBERNETES_GPU_QUANTITY_REQUIRED")
    return normalized


def _distributed_launch(record: dict) -> list[str] | None:
    world_size = int(record.get("world_size") or 1)
    gpu_count = _requested_gpu_count(record)
    if world_size == 1:
        return None
    if not gpu_count or not gpu_count.strip().isdigit() or int(gpu_count.strip()) < world_size:
        raise ValueError("TRAINING_DISTRIBUTED_GPU_CAPACITY_REQUIRED")
    return ["torchrun", "--standalone", "--nproc_per_node", str(world_size), "/app/train.py"]


def _node_count(record: dict) -> int:
    return int(record.get("node_count") or 1)


def _validate_distributed_topology(record: dict, backend: str) -> None:
    nodes = _node_count(record)
    if nodes > 1 and backend != "kubernetes":
        raise ValueError("TRAINING_MULTINODE_KUBERNETES_REQUIRED")
    if nodes > 1:
        _distributed_launch(record)


def _pytorchjob_launch(record: dict) -> list[str]:
    _distributed_launch(record)
    nodes = _node_count(record)
    processes = int(record.get("world_size") or 1)
    command = (
        "exec torchrun "
        f"--nnodes={nodes} --nproc_per_node={processes} "
        '--node_rank="${RANK}" --master_addr="${MASTER_ADDR}" '
        '--master_port="${MASTER_PORT}" /app/train.py'
    )
    return ["/bin/sh", "-ec", command]


def _build_kubernetes_job_manifest(record: dict) -> dict:
    path, model = _training_paths(record)
    settings = get_settings()
    input_sub_path = _kubernetes_sub_path(path / "input")
    output_sub_path = _kubernetes_sub_path(path / "output")
    model_sub_path = _kubernetes_sub_path(model)
    resources = {"cpu": settings.training_cpu_limit, "memory": settings.training_memory_limit}
    gpu_limit = _kubernetes_gpu_limit(_requested_gpu_count(record))
    launch = _distributed_launch(record)
    if gpu_limit:
        resources["nvidia.com/gpu"] = gpu_limit
    job_name = training_job_name(record)
    container = {
        "name": "trainer",
        "image": settings.training_image,
        "imagePullPolicy": settings.training_kubernetes_image_pull_policy,
        "env": [{"name": "HF_HOME", "value": "/tmp/huggingface"}],
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "resources": {"limits": resources},
        "volumeMounts": [
            {"name": "workspace", "mountPath": "/input", "subPath": input_sub_path, "readOnly": True},
            {"name": "workspace", "mountPath": "/output", "subPath": output_sub_path, "readOnly": False},
            {"name": "workspace", "mountPath": "/model", "subPath": model_sub_path, "readOnly": True},
            {"name": "tmp", "mountPath": "/tmp", "readOnly": False},
        ],
    }
    if launch:
        container["command"] = launch
    pod_spec: dict[str, object] = {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "securityContext": {"runAsNonRoot": True},
        "containers": [container],
        "volumes": [
            {"name": "workspace", "persistentVolumeClaim": {"claimName": settings.training_kubernetes_workspace_pvc}},
            {"name": "tmp", "emptyDir": {"medium": "Memory", "sizeLimit": "2Gi"}},
        ],
    }
    if settings.training_kubernetes_service_account:
        pod_spec["serviceAccountName"] = settings.training_kubernetes_service_account
    if settings.training_kubernetes_node_selector:
        pod_spec["nodeSelector"] = settings.training_kubernetes_node_selector
    if settings.training_kubernetes_tolerations:
        pod_spec["tolerations"] = settings.training_kubernetes_tolerations
    labels = {
        "app.kubernetes.io/name": "researchforge-training",
        "app.kubernetes.io/managed-by": "researchforge",
        "researchforge.training/job-id": str(record["id"]),
    }
    spec: dict[str, object] = {
        "backoffLimit": 0,
        "activeDeadlineSeconds": int(record["max_seconds"]),
        "template": {"metadata": {"labels": labels}, "spec": pod_spec},
    }
    if not settings.training_kubernetes_keep_jobs:
        spec["ttlSecondsAfterFinished"] = 3600
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": job_name, "namespace": settings.training_kubernetes_namespace, "labels": labels},
        "spec": spec,
    }


def build_kubernetes_pytorchjob_manifest(record: dict) -> dict:
    """Build a Training Operator v1 PyTorchJob for a multi-node DDP launch."""
    if _node_count(record) <= 1:
        raise ValueError("TRAINING_MULTINODE_NODE_COUNT_REQUIRED")
    base = _build_kubernetes_job_manifest(record)
    job_name = training_job_name(record)
    labels = dict(base["metadata"]["labels"])
    labels["training.kubeflow.org/job-name"] = job_name
    pod_template = base["spec"]["template"]
    pod_template["metadata"]["labels"] = labels
    pod_template["metadata"].setdefault("annotations", {})["sidecar.istio.io/inject"] = "false"
    container = pod_template["spec"]["containers"][0]
    container["command"] = _pytorchjob_launch(record)
    return {
        "apiVersion": "kubeflow.org/v1",
        "kind": "PyTorchJob",
        "metadata": {"name": job_name, "namespace": get_settings().training_kubernetes_namespace, "labels": labels},
        "spec": {
            "runPolicy": {
                "cleanPodPolicy": "None" if get_settings().training_kubernetes_keep_jobs else "Running",
                "activeDeadlineSeconds": int(record["max_seconds"]),
            },
            "pytorchReplicaSpecs": {
                "Master": {"replicas": 1, "restartPolicy": "Never", "template": pod_template},
                "Worker": {"replicas": _node_count(record) - 1, "restartPolicy": "Never", "template": pod_template},
            },
        },
    }


def build_kubernetes_manifest(record: dict) -> dict:
    _validate_distributed_topology(record, "kubernetes")
    if _node_count(record) > 1:
        return build_kubernetes_pytorchjob_manifest(record)
    return _build_kubernetes_job_manifest(record)


def _start_docker_training(store, record: dict, path: Path, model: Path):
    name = "rf-" + record["id"]
    existing = docker("inspect", name, check=False)
    if existing.returncode:
        args = ["create", "--name", name, "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--read-only",
                "--memory", get_settings().training_memory_limit, "--cpus", get_settings().training_cpu_limit,
                "--pids-limit", "256", "--tmpfs", "/tmp:rw,nosuid,size=1g", "--log-opt", "max-size=10m", "--log-opt", "max-file=2",
                "--mount", f"type=bind,source={path / 'input'},target=/input,readonly",
                "--mount", f"type=bind,source={path / 'output'},target=/output",
                "--mount", f"type=bind,source={model},target=/model,readonly"]
        gpu_count = _requested_gpu_count(record)
        if gpu_count:
            args.extend(["--gpus", gpu_count])
        launch = _distributed_launch(record)
        if launch:
            args.extend(["--entrypoint", launch[0]])
        args.append(get_settings().training_image)
        if launch:
            args.extend(launch[1:])
        try:
            docker(*args)
        except Exception:
            # A concurrent start can have created the same deterministic container.
            docker("inspect", name)
    state = json.loads(docker("inspect", name).stdout)[0]["State"]
    if state["Status"] == "created":
        docker("start", name)
    record["status"] = "running"
    record["backend"] = "docker"


def _start_kubernetes_training(record: dict):
    manifest = build_kubernetes_manifest(record)
    namespace = get_settings().training_kubernetes_namespace
    name = str(manifest["metadata"]["name"])
    resource = str(manifest["kind"]).lower()
    existing = kubectl("get", resource, name, "-n", namespace, "-o", "json", check=False)
    if existing.returncode:
        with TemporaryDirectory(prefix="researchforge-training-job-") as directory:
            manifest_path = Path(directory) / "job.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            kubectl("apply", "-f", str(manifest_path), "-n", namespace)
    record.update(status="running", backend="kubernetes", backend_job_name=name, backend_resource=resource)


def start_training(store, record):
    if record["status"] not in {"prepared", "starting"}:
        raise ValueError("TRAINING_NOT_STARTABLE")
    backend = training_backend()
    _validate_distributed_topology(record, backend)
    path, model = _training_paths(record)
    record.update(status="starting", started_at=record.get("started_at") or now())
    store._persist()
    if backend == "docker":
        _start_docker_training(store, record, path, model)
    else:
        _start_kubernetes_training(record)
    store._persist()
    return refresh_training(store, record)


def _finalize_training(store, record: dict, *, succeeded: bool, exit_code: int | None):
    record.update(status="succeeded" if succeeded else "failed", finished_at=now(), exit_code=exit_code)
    if not succeeded:
        store._persist()
        return record
    metrics_file = job_dir(record["id"]) / "output/metrics.json"
    model_dir = metrics_file.parent / "model"
    weights = list(model_dir.glob("*.safetensors")) if model_dir.is_dir() else []
    if not weights or model_dir.is_symlink() or any(path.is_symlink() for path in weights) or not (model_dir / "config.json").is_file():
        record.update(status="failed", error="TRAINING_MODEL_ARTIFACT_MISSING")
    elif not metrics_file.is_file() or metrics_file.is_symlink() or metrics_file.stat().st_size > 1_000_000:
        record.update(status="failed", error="TRAINING_METRICS_MISSING")
    else:
        record["metrics"] = json.loads(metrics_file.read_text(encoding="utf-8"))
        version_id = "version_" + record["id"][6:]
        store.model_versions.setdefault(version_id, {"id": version_id, "training_job_id": record["id"], "workspace_id": record["workspace_id"],
            "status": "candidate", "model_name": version_id, "dataset_sha256": record["dataset_sha256"], "created_at": now(),
            "artifact_path": str(job_dir(record["id"]) / "output/model"), "metrics": record["metrics"]})
        record["version_id"] = version_id
    store._persist()
    return record


def _refresh_docker_training(store, record: dict):
    state = json.loads(docker("inspect", "rf-" + record["id"]).stdout)[0]["State"]
    if state["Running"]:
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(record["started_at"])).total_seconds()
        if elapsed > record["max_seconds"]:
            docker("stop", "--time", "5", "rf-" + record["id"])
            record.update(status="failed", error="TRAINING_TIME_BUDGET_EXCEEDED", finished_at=now())
    elif state["Status"] in {"exited", "dead"}:
        return _finalize_training(store, record, succeeded=state["ExitCode"] == 0, exit_code=state["ExitCode"])
    store._persist()
    return record


def _kubernetes_job_payload(record: dict) -> dict:
    namespace = get_settings().training_kubernetes_namespace
    name = str(record.get("backend_job_name") or training_job_name(record))
    result = kubectl("get", _kubernetes_resource_kind(record), name, "-n", namespace, "-o", "json", check=False)
    if result.returncode:
        raise RuntimeError("TRAINING_KUBERNETES_JOB_NOT_FOUND")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("TRAINING_KUBERNETES_INVALID_RESPONSE") from exc
    return payload if isinstance(payload, dict) else {}


def _kubernetes_resource_kind(record: dict) -> str:
    resource = str(record.get("backend_resource") or ("pytorchjob" if _node_count(record) > 1 else "job")).lower()
    if resource not in {"job", "pytorchjob"}:
        raise ValueError("TRAINING_KUBERNETES_RESOURCE_UNSUPPORTED")
    return resource


def _job_condition(status: dict, condition_type: str) -> dict | None:
    for condition in status.get("conditions") or []:
        if isinstance(condition, dict) and condition.get("type") == condition_type and condition.get("status") == "True":
            return condition
    return None


def _get_kubernetes_logs(record: dict) -> str:
    namespace = get_settings().training_kubernetes_namespace
    name = str(record.get("backend_job_name") or training_job_name(record))
    if _kubernetes_resource_kind(record) == "pytorchjob":
        result = kubectl("logs", "-l", f"training.kubeflow.org/job-name={name}", "-n", namespace, "--all-containers=true", "--prefix=true", "--tail", "200", check=False)
    else:
        result = kubectl("logs", f"job/{name}", "-n", namespace, "--tail", "200", check=False)
    text = (result.stdout + result.stderr)[-64_000:]
    return text


def _refresh_kubernetes_training(store, record: dict):
    try:
        payload = _kubernetes_job_payload(record)
    except RuntimeError:
        if record.get("backend_terminal"):
            return record
        raise
    status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
    succeeded = _job_condition(status, "Complete") or _job_condition(status, "Succeeded") or int(status.get("succeeded") or 0) > 0
    failed = _job_condition(status, "Failed") or int(status.get("failed") or 0) > 0
    if succeeded or failed:
        record["backend_log"] = _get_kubernetes_logs(record)
        record["backend_terminal"] = True
        result = _finalize_training(store, record, succeeded=bool(succeeded), exit_code=0 if succeeded else 1)
        if not get_settings().training_kubernetes_keep_jobs:
            name = str(record.get("backend_job_name") or training_job_name(record))
            kubectl("delete", _kubernetes_resource_kind(record), name, "-n", get_settings().training_kubernetes_namespace, "--ignore-not-found=true", check=False)
        return result
    elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(record["started_at"])).total_seconds()
    if elapsed > record["max_seconds"]:
        name = str(record.get("backend_job_name") or training_job_name(record))
        kubectl("delete", _kubernetes_resource_kind(record), name, "-n", get_settings().training_kubernetes_namespace, "--ignore-not-found=true", check=False)
        record.update(status="failed", error="TRAINING_TIME_BUDGET_EXCEEDED", finished_at=now(), backend_terminal=True)
    else:
        record["status"] = "running"
    store._persist()
    return record


def refresh_training(store, record):
    if record["status"] not in {"starting", "running"}:
        return record
    return _refresh_docker_training(store, record) if _record_backend(record) == "docker" else _refresh_kubernetes_training(store, record)


def cancel_training(store, record):
    if record["status"] in {"succeeded", "failed", "cancelled"}:
        return record
    if record["status"] in {"running", "starting"}:
        if _record_backend(record) == "docker":
            docker("stop", "--time", "5", "rf-" + record["id"])
        else:
            name = str(record.get("backend_job_name") or training_job_name(record))
            kubectl("delete", _kubernetes_resource_kind(record), name, "-n", get_settings().training_kubernetes_namespace, "--ignore-not-found=true", check=False)
    record.update(status="cancelled", finished_at=now(), backend_terminal=True)
    store._persist()
    return record


def get_training_logs(record: dict) -> str:
    if _record_backend(record) == "docker":
        result = docker("logs", "--tail", "200", "rf-" + record["id"])
        return (result.stdout + result.stderr)[-64_000:]
    return str(record.get("backend_log") or _get_kubernetes_logs(record))[-64_000:]


def validate_model_release(store, version, model_config_id, evaluation_id):
    model = store.model_configs.get(model_config_id)
    evaluation = store.get_evaluation_run(evaluation_id)
    if not model or model.provider == "mock" or model.model_name != version["model_name"]:
        raise ValueError("REGISTER_CANDIDATE_INFERENCE_ENDPOINT_FIRST")
    if not evaluation or evaluation.status != RunStatus.COMPLETED or evaluation.model_name != model.model_name or not evaluation.items or not evaluation.finished_at:
        raise ValueError("COMPLETED_MODEL_EVALUATION_REQUIRED")
    if version.get("status") == "active":
        raise ValueError("MODEL_VERSION_ALREADY_ACTIVE")
    minimum = float(os.getenv("RESEARCHFORGE_MODEL_MIN_SUCCESS_RATE", "0.8"))
    successes = sum(item.success for item in evaluation.items) / len(evaluation.items)
    for item in evaluation.items:
        run = store.get_run(item.agent_run_id) if item.agent_run_id else None
        task = store.get_task(run.task_id) if run else None
        if not run or not task or task.workspace_id != version["workspace_id"]:
            raise ValueError("EVALUATION_WORKSPACE_MISMATCH")
        if item.task_id != task.id or (item.success and run.status != RunStatus.COMPLETED):
            raise ValueError("EVALUATION_RUN_STATE_MISMATCH")
        patches = [artifact for artifact in store.list_artifacts(run.id) if str(artifact.type) == "diff"]
        if not patches or any(item.metadata.get("patch_source") != "model" for item in patches) or run.model_name != model.model_name:
            raise ValueError("MODEL_ONLY_EVALUATION_REQUIRED")
    if successes < minimum or evaluation.summary.get("policy_violation_count", 0) or evaluation.summary.get("regression_count", 0):
        raise ValueError("MODEL_RELEASE_GATE_FAILED")
    return model, evaluation


def promote_model(store, version, model_config_id, evaluation_id, actor_id):
    model, evaluation = validate_model_release(store, version, model_config_id, evaluation_id)
    deployment_id = "deployment_" + uuid4().hex
    previous = [item.id for item in store.model_configs.values() if item.role == model.role and item.status == "active"]
    store.model_deployments[deployment_id] = {"id": deployment_id, "workspace_id": version["workspace_id"], "version_id": version["id"],
        "model_config_id": model.id, "previous_active_ids": previous, "evaluation_id": evaluation.id, "created_at": now(), "status": "active"}
    for item in store.model_configs.values():
        if item.role == model.role and item.status == "active":
            item.status = "inactive"
    model.status = "active"
    version["status"] = "active"
    store.add_audit_log(action="model.promote", resource_type="model_deployment", resource_id=deployment_id, actor_id=actor_id, decision="allow")
    store._persist()
    return store.model_deployments[deployment_id]


def rollback_model(store, deployment, actor_id):
    if deployment["status"] != "active":
        raise ValueError("DEPLOYMENT_NOT_ACTIVE")
    model = store.model_configs[deployment["model_config_id"]]
    latest = max((item for item in store.model_deployments.values() if item["status"] == "active" and store.model_configs[item["model_config_id"]].role == model.role), key=lambda item: item["created_at"])
    if latest["id"] != deployment["id"]:
        raise ValueError("ONLY_LATEST_DEPLOYMENT_CAN_ROLL_BACK")
    model.status = "inactive"
    for model_id in deployment["previous_active_ids"]:
        if model_id in store.model_configs:
            store.model_configs[model_id].status = "active"
    deployment["status"] = "rolled_back"
    store.model_versions[deployment["version_id"]]["status"] = "candidate"
    store.add_audit_log(action="model.rollback", resource_type="model_deployment", resource_id=deployment["id"], actor_id=actor_id, decision="allow")
    store._persist()
    return deployment
