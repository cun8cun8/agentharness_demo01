from fastapi import APIRouter

from fastapi import HTTPException, Query, Request

from app.api.context import is_admin
from app.config import get_settings
from app.services.sandbox_runner import sandbox_runner
from app.services.sandbox_pool import sandbox_pool

router = APIRouter(tags=["sandbox"])


@router.get("/sandbox/status")
async def sandbox_status() -> dict[str, object]:
    settings = get_settings()
    probe = sandbox_runner.probe()
    return {
        "backend": sandbox_runner.backend,
        "configured_backend": settings.sandbox_backend,
        "image": settings.sandbox_image,
        "network_enabled": settings.network_enabled,
        "isolation": ("docker" if sandbox_runner.backend == "docker" else "kubernetes" if sandbox_runner.backend == "kubernetes" else "local-tempdir"),
        "ready": probe["ready"],
        "version": probe.get("version"),
        "image_present": probe.get("image_present"),
        "network": probe.get("network"),
        "limits": probe.get("limits"),
        "message": probe["message"],
        "pool": sandbox_pool.status(),
    }


@router.get("/sandbox/docker-command")
async def sandbox_docker_command(repo_path: str = "/workspace") -> dict[str, object]:
    settings = get_settings()
    # This endpoint is a preview, so keep the caller's path as the host mount.
    network_flag = sandbox_runner.policy_summary()["network"]
    return {
        "command": sandbox_runner.build_docker_preview_command(
            ["python", "-m", "pytest", "-q"],
            host_path=repo_path,
        ),
        "network": network_flag,
        "limits": sandbox_runner.policy_summary()["limits"],
    }


@router.get("/sandbox/kubernetes-manifest")
async def sandbox_kubernetes_manifest(repo_path: str = "/workspace") -> dict[str, object]:
    settings = get_settings()
    policy = sandbox_runner.policy_summary()
    manifest = sandbox_runner.build_kubernetes_preview_manifest(
        ["python", "-m", "pytest", "-q"],
        host_path=repo_path,
    )
    resources = [
        manifest,
        sandbox_runner.build_kubernetes_network_policy(
            str(manifest["metadata"]["name"])
        ),
    ]
    return {
        "backend": sandbox_runner.backend,
        "manifest": manifest,
        "resources": resources,
        "network": policy["network"],
        "network_policy": policy["kubernetes"].get("network_policy", True),
        "limits": policy["limits"],
        "namespace": settings.sandbox_kubernetes_namespace,
        "workspace_pvc": settings.sandbox_kubernetes_workspace_pvc,
    }


@router.get("/sandbox/check")
async def sandbox_check(
    request: Request,
    verify_execution: bool = Query(default=False),
) -> dict[str, object]:
    if verify_execution and not is_admin(request):
        raise HTTPException(403, "ADMIN_REQUIRED")
    settings = get_settings()
    probe = sandbox_runner.execution_probe() if verify_execution else sandbox_runner.probe()
    probe["configured_backend"] = settings.sandbox_backend
    return probe


@router.get("/sandbox/pool")
async def sandbox_pool_status() -> dict[str, object]:
    return sandbox_pool.status()
