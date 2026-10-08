from __future__ import annotations

import json
import subprocess
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Sequence
from uuid import uuid4

from app.config import Settings, get_settings
from app.services.sandbox_pool import SandboxCapacityExceeded, sandbox_pool


class SandboxUnavailable(RuntimeError):
    """Raised when the configured sandbox backend cannot be started."""


@dataclass(frozen=True)
class SandboxCommandResult:
    returncode: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    backend: str = "local"
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    output_limit_bytes: int = 64_000


class SandboxRunner:
    """Execute tool commands through the configured local, Docker, or Kubernetes backend."""

    @property
    def backend(self) -> str:
        backend = get_settings().sandbox_backend.strip().lower() or "local"
        return "kubernetes" if backend == "k8s" else backend

    def build_docker_command(
        self,
        args: Sequence[str],
        workspace_root: str | Path,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
    ) -> list[str]:
        root = Path(workspace_root).expanduser().resolve()
        working_directory = Path(cwd or root).expanduser().resolve()
        try:
            relative_cwd = working_directory.relative_to(root)
        except ValueError as exc:
            raise SandboxUnavailable("PATH_OUTSIDE_REPO") from exc

        container_cwd = "/workspace"
        if relative_cwd != Path("."):
            container_cwd = f"/workspace/{relative_cwd.as_posix()}"
        settings = get_settings()
        network = self._docker_network(settings)
        workspace_volume = os.getenv("RESEARCHFORGE_SANDBOX_WORKSPACE_VOLUME", "").strip()
        mount = ["-v", f"{root}:/workspace:{self._mount_mode(settings.sandbox_mount_mode)}"]
        if workspace_volume:
            if not settings.sandbox_shared_workspace_root:
                raise SandboxUnavailable("SHARED_WORKSPACE_ROOT_REQUIRED")
            shared_root = Path(settings.sandbox_shared_workspace_root).expanduser().resolve()
            try:
                subpath = root.relative_to(shared_root).as_posix()
            except ValueError as exc:
                raise SandboxUnavailable("PATH_OUTSIDE_SHARED_WORKSPACE") from exc
            options = f"type=volume,source={workspace_volume},target=/workspace"
            if subpath != ".":
                options += f",volume-subpath={subpath}"
            if self._mount_mode(settings.sandbox_mount_mode) == "ro":
                options += ",readonly"
            mount = ["--mount", options]
        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            network,
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--memory",
            settings.sandbox_memory_limit,
            "--cpus",
            str(settings.sandbox_cpu_limit),
            "--pids-limit",
            str(max(1, int(settings.sandbox_pids_limit))),
            "--label",
            "researchforge.sandbox.ephemeral=true",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,size={settings.sandbox_tmpfs_size}",
            *mount,
            "-w",
            container_cwd,
            settings.sandbox_image,
            *[str(item) for item in args],
        ]
        if env:
            insert_at = command.index(settings.sandbox_image)
            env_args = [item for key, value in sorted(env.items()) for item in ("-e", f"{key}={value}")]
            command[insert_at:insert_at] = env_args
        if settings.sandbox_user:
            insert_at = command.index("--memory")
            command[insert_at:insert_at] = ["--user", settings.sandbox_user]
        return command

    def build_docker_preview_command(
        self,
        args: Sequence[str],
        host_path: str,
    ) -> list[str]:
        """Build a UI/API preview without resolving the illustrative host path."""
        settings = get_settings()
        network = self._docker_network(settings)
        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            network,
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--memory",
            settings.sandbox_memory_limit,
            "--cpus",
            str(settings.sandbox_cpu_limit),
            "--pids-limit",
            str(max(1, int(settings.sandbox_pids_limit))),
            "--label",
            "researchforge.sandbox.ephemeral=true",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,size={settings.sandbox_tmpfs_size}",
            "-v",
            f"{host_path}:/workspace:{self._mount_mode(settings.sandbox_mount_mode)}",
            "-w",
            "/workspace",
            settings.sandbox_image,
            *[str(item) for item in args],
        ]
        if settings.sandbox_user:
            insert_at = command.index("--memory")
            command[insert_at:insert_at] = ["--user", settings.sandbox_user]
        return command

    def build_kubernetes_manifest(
        self,
        args: Sequence[str],
        workspace_root: str | Path,
        cwd: str | Path | None = None,
        pod_name: str | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, object]:
        root = Path(workspace_root).expanduser().resolve()
        working_directory = Path(cwd or root).expanduser().resolve()
        try:
            relative_cwd = working_directory.relative_to(root)
        except ValueError as exc:
            raise SandboxUnavailable("PATH_OUTSIDE_REPO") from exc
        container_cwd = "/workspace"
        if relative_cwd != Path("."):
            container_cwd = f"/workspace/{relative_cwd.as_posix()}"
        settings = get_settings()
        host_path = str(root)
        if settings.sandbox_kubernetes_workspace_pvc and settings.sandbox_shared_workspace_root:
            shared_root = Path(settings.sandbox_shared_workspace_root).expanduser().resolve()
            try:
                relative_root = root.relative_to(shared_root)
            except ValueError as exc:
                raise SandboxUnavailable("KUBERNETES_WORKSPACE_NOT_SHARED") from exc
            host_path = "/workspace/" + relative_root.as_posix()
        return self._kubernetes_manifest(
            args,
            host_path=host_path,
            container_cwd=container_cwd,
            pod_name=pod_name,
            env=env,
        )
    def build_kubernetes_preview_manifest(
        self,
        args: Sequence[str],
        host_path: str = "/workspace",
        pod_name: str = "researchforge-sandbox-preview",
    ) -> dict[str, object]:
        """Build a UI/API preview without resolving the illustrative host path."""
        return self._kubernetes_manifest(
            args,
            host_path=host_path,
            container_cwd="/workspace",
            pod_name=pod_name,
        )

    def _kubernetes_manifest(
        self,
        args: Sequence[str],
        *,
        host_path: str,
        container_cwd: str,
        pod_name: str | None,
        env: dict[str, str] | None = None,
    ) -> dict[str, object]:
        settings = get_settings()
        production = settings.environment.strip().lower() in {"production", "prod"}
        if production and not settings.sandbox_kubernetes_workspace_pvc:
            raise SandboxUnavailable("PRODUCTION_KUBERNETES_PVC_REQUIRED")
        mount_mode = self._mount_mode(settings.sandbox_mount_mode)
        pod_name = pod_name or f"researchforge-sandbox-{uuid4().hex[:12]}"
        security_context: dict[str, object] = {
            "allowPrivilegeEscalation": False,
            "runAsNonRoot": True,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
            "seccompProfile": {"type": "RuntimeDefault"},
        }
        if settings.sandbox_user:
            user_parts = settings.sandbox_user.split(":", 1)
            if user_parts[0].isdigit():
                security_context["runAsUser"] = int(user_parts[0])
            if len(user_parts) > 1 and user_parts[1].isdigit():
                security_context["runAsGroup"] = int(user_parts[1])
        container = {
            "name": "runner",
            "image": settings.sandbox_image,
            "imagePullPolicy": settings.sandbox_kubernetes_image_pull_policy,
            "command": [str(item) for item in args],
            "workingDir": container_cwd,
            "securityContext": security_context,
            "resources": {
                "limits": {
                    "memory": settings.sandbox_memory_limit,
                    "cpu": str(settings.sandbox_cpu_limit),
                }
            },
            "volumeMounts": [
                {
                    "name": "workspace",
                    "mountPath": "/workspace",
                    "readOnly": mount_mode == "ro",
                },
                {
                    "name": "tmp",
                    "mountPath": "/tmp",
                    "readOnly": False,
                },
            ],
        }
        if env:
            container["env"] = [{"name": key, "value": value} for key, value in sorted(env.items())]
        workspace_volume: dict[str, object] = {"name": "workspace"}
        if settings.sandbox_kubernetes_workspace_pvc:
            pvc = {
                "claimName": settings.sandbox_kubernetes_workspace_pvc,
                "readOnly": mount_mode == "ro",
            }
            if settings.sandbox_shared_workspace_root and host_path.startswith("/workspace/"):
                sub_path = host_path.removeprefix("/workspace/").strip("/")
                if sub_path and pod_name != "researchforge-sandbox-preview":
                    pvc["subPath"] = sub_path
            workspace_volume["persistentVolumeClaim"] = pvc
        else:
            workspace_volume["hostPath"] = {
                "path": host_path,
                "type": "Directory",
            }
        spec: dict[str, object] = {
            "restartPolicy": "Never",
            "activeDeadlineSeconds": max(
                1,
                int(settings.sandbox_kubernetes_active_deadline_seconds),
            ),
            "automountServiceAccountToken": False,
            "containers": [container],
            "volumes": [
                workspace_volume,
                {
                    "name": "tmp",
                    "emptyDir": {
                        "medium": "Memory",
                        "sizeLimit": settings.sandbox_tmpfs_size,
                    },
                },
            ],
        }
        if settings.sandbox_kubernetes_runtime_class:
            spec["runtimeClassName"] = settings.sandbox_kubernetes_runtime_class
        if settings.sandbox_kubernetes_service_account:
            spec["serviceAccountName"] = settings.sandbox_kubernetes_service_account
        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": pod_name,
                "namespace": self._kubernetes_namespace(settings),
                "labels": {
                    "app.kubernetes.io/name": "researchforge-sandbox",
                    "app.kubernetes.io/managed-by": "researchforge",
                    "researchforge.sandbox/pod-name": pod_name,
                },
            },
            "spec": spec,
        }

    def build_kubernetes_network_policy(self, pod_name: str) -> dict[str, object]:
        settings = get_settings()
        return {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": f"{pod_name}-network",
                "namespace": self._kubernetes_namespace(settings),
                "labels": {
                    "app.kubernetes.io/name": "researchforge-sandbox",
                    "app.kubernetes.io/managed-by": "researchforge",
                },
            },
            "spec": {
                "podSelector": {
                    "matchLabels": {"researchforge.sandbox/pod-name": pod_name}
                },
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [],
                # Public egress is never implicit. Development keeps the old
                # opt-in open rule for compatibility; production must provide
                # explicit NetworkPolicy rules.
                "egress": (
                    list(settings.sandbox_kubernetes_egress_rules)
                    if settings.sandbox_network_enabled and settings.sandbox_kubernetes_egress_rules
                    else ([{}] if settings.sandbox_network_enabled and settings.environment not in {"production", "prod"} else [])
                ),
            },
        }

    def build_kubernetes_resources(
        self,
        args: Sequence[str],
        workspace_root: str | Path,
        cwd: str | Path | None = None,
        pod_name: str | None = None,
        env: dict[str, str] | None = None,
    ) -> list[dict[str, object]]:
        pod = self.build_kubernetes_manifest(args, workspace_root, cwd, pod_name=pod_name, env=env)
        return [pod, self.build_kubernetes_network_policy(str(pod["metadata"]["name"]))]

    def build_kubernetes_preview_resources(
        self,
        args: Sequence[str],
        host_path: str = "/workspace",
        pod_name: str = "researchforge-sandbox-preview",
    ) -> list[dict[str, object]]:
        pod = self.build_kubernetes_preview_manifest(args, host_path, pod_name)
        return [pod, self.build_kubernetes_network_policy(pod_name)]

    def probe(self) -> dict[str, object]:
        settings = get_settings()
        backend = self.backend
        if backend == "local":
            return {
                "backend": backend,
                "ready": True,
                "version": None,
                "image": settings.sandbox_image,
                "image_present": None,
                "network": self._network_label(settings, self.backend),
                "limits": self.policy_summary()["limits"],
                "message": "当前使用本地临时目录沙箱，无需 Docker 探测。",
            }
        if backend == "kubernetes":
            return self._probe_kubernetes(settings)
        if backend != "docker":
            return {
                "backend": backend,
                "ready": False,
                "version": None,
                "image": settings.sandbox_image,
                "image_present": False,
                "network": self._network_label(settings, self.backend),
                "limits": self.policy_summary()["limits"],
                "message": f"不支持的沙箱后端：{backend}。",
            }
        try:
            result = subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "backend": backend,
                "ready": False,
                "version": None,
                "image": settings.sandbox_image,
                "image_present": False,
                "network": self._network_label(settings, self.backend),
                "limits": self.policy_summary()["limits"],
                "message": f"Docker 不可用：{exc}",
            }
        version = result.stdout.strip()
        image_present = False
        image_message = ""
        if result.returncode == 0:
            try:
                image_result = subprocess.run(
                    ["docker", "image", "inspect", settings.sandbox_image],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                image_message = f"Docker 镜像检查失败：{exc}"
            else:
                image_present = image_result.returncode == 0
                image_message = image_result.stderr.strip()
        return {
            "backend": backend,
            "ready": result.returncode == 0 and image_present,
            "version": version or None,
            "image": settings.sandbox_image,
            "image_present": image_present,
            "network": self._network_label(settings, self.backend),
            "limits": self.policy_summary()["limits"],
            "message": (
                "Docker 沙箱可用。"
                if result.returncode == 0 and image_present
                else image_message or result.stderr.strip()
            ),
        }

    def execution_probe(self, timeout_seconds: int = 15) -> dict[str, object]:
        """Execute a fixed, no-network command to validate the configured isolator.

        Unlike :meth:`probe`, this verifies that the sandbox image can start with
        its configured security controls. It never mounts a repository or accepts
        caller input, so it is safe to expose as an administrator-only diagnostic.
        """
        probe = self.probe()
        backend = self.backend
        base = {
            **probe,
            "execution_ready": False,
            "reason": None,
            "duration_ms": None,
            "returncode": None,
        }
        if backend == "local":
            return {
                **base,
                "reason": "LOCAL_SANDBOX_NOT_ISOLATED",
                "message": "本地临时目录模式不满足隔离沙箱执行验证。",
            }
        if not probe.get("ready"):
            return {
                **base,
                "reason": "SANDBOX_CONTROL_PLANE_UNAVAILABLE",
                "message": str(probe.get("message") or "沙箱控制面不可用。"),
            }
        args = ["python", "-c", "print('RESEARCHFORGE_SANDBOX_PROBE_OK')"]
        try:
            with sandbox_pool.lease(backend):
                if backend == "docker":
                    result = self._run_buffered_process(
                        self._docker_execution_probe_command(args),
                        cwd=None,
                        timeout_seconds=min(30, max(1, int(timeout_seconds))),
                        backend="docker",
                    )
                elif backend == "kubernetes":
                    result = self._run_kubernetes_execution_probe(
                        args,
                        timeout_seconds=min(60, max(1, int(timeout_seconds))),
                    )
                else:
                    raise SandboxUnavailable(f"UNSUPPORTED_SANDBOX_BACKEND:{backend}")
        except (SandboxUnavailable, OSError) as exc:
            return {
                **base,
                "reason": "SANDBOX_EXECUTION_UNAVAILABLE",
                "message": str(exc),
            }
        ready = (
            result.returncode == 0
            and not result.timed_out
            and "RESEARCHFORGE_SANDBOX_PROBE_OK" in result.stdout
        )
        return {
            **base,
            "execution_ready": ready,
            "reason": "SANDBOX_EXECUTION_READY" if ready else "SANDBOX_COMMAND_FAILED",
            "duration_ms": result.duration_ms,
            "returncode": result.returncode,
            "message": "隔离沙箱执行验证通过。" if ready else "隔离沙箱未能执行固定自检命令。",
        }

    def _docker_execution_probe_command(self, args: Sequence[str]) -> list[str]:
        command = self.build_docker_preview_command(args, host_path="/workspace-unused")
        mount_index = command.index("-v")
        del command[mount_index : mount_index + 2]
        command[command.index("--network") + 1] = "none"
        return command

    def _run_kubernetes_execution_probe(
        self,
        args: Sequence[str],
        *,
        timeout_seconds: int,
    ) -> SandboxCommandResult:
        settings = get_settings()
        pod_name = f"researchforge-sandbox-probe-{uuid4().hex[:12]}"
        pod = self._kubernetes_manifest(
            args,
            host_path="/workspace",
            container_cwd="/workspace",
            pod_name=pod_name,
        )
        spec = pod["spec"]
        assert isinstance(spec, dict)
        volumes = spec["volumes"]
        assert isinstance(volumes, list)
        volumes[0] = {
            "name": "workspace",
            "emptyDir": {"medium": "Memory", "sizeLimit": settings.sandbox_tmpfs_size},
        }
        containers = spec["containers"]
        assert isinstance(containers, list) and isinstance(containers[0], dict)
        mounts = containers[0]["volumeMounts"]
        assert isinstance(mounts, list) and isinstance(mounts[0], dict)
        mounts[0]["readOnly"] = False
        policy = self.build_kubernetes_network_policy(pod_name)
        policy["spec"]["egress"] = []
        return self._execute_kubernetes_resources(
            [pod, policy],
            pod_name=pod_name,
            timeout_seconds=timeout_seconds,
        )

    def _probe_kubernetes(self, settings: Settings) -> dict[str, object]:
        namespace = self._kubernetes_namespace(settings)
        base = self._kubectl_base_command(settings)
        result = {
            "backend": "kubernetes",
            "ready": False,
            "version": None,
            "image": settings.sandbox_image,
            "image_present": None,
            "namespace": namespace,
            "workspace_pvc": settings.sandbox_kubernetes_workspace_pvc,
            "network": self._network_label(settings, "kubernetes"),
            "network_policy": True,
            "limits": self.policy_summary()["limits"],
        }
        try:
            version = subprocess.run(
                [*base, "version", "--client", "--output=json"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if version.returncode != 0:
                return {**result, "message": version.stderr.strip() or "kubectl 客户端探测失败。"}
            pod_auth = subprocess.run(
                [*base, "auth", "can-i", "create", "pods", "-n", namespace],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            policy_auth = subprocess.run(
                [*base, "auth", "can-i", "create", "networkpolicies", "-n", namespace],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {**result, "message": f"kubectl 不可用：{exc}"}
        pod_allowed = pod_auth.returncode == 0 and pod_auth.stdout.strip().lower() == "yes"
        policy_allowed = policy_auth.returncode == 0 and policy_auth.stdout.strip().lower() == "yes"
        allowed = pod_allowed and policy_allowed
        message = "Kubernetes 沙箱可用。"
        if not allowed:
            denied = policy_auth if pod_allowed else pod_auth
            message = (
                denied.stderr.strip()
                or denied.stdout.strip()
                or "当前身份无权创建 Kubernetes 沙箱资源。"
            )
        return {
            **result,
            "ready": allowed,
            "version": self._kubernetes_client_version(version.stdout),
            "message": message,
        }
    def policy_summary(self) -> dict[str, object]:
        settings = get_settings()
        return {
            "backend": self.backend,
            "network": self._network_label(settings, self.backend),
            "read_only_rootfs": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges"],
            "workspace_mount_mode": self._mount_mode(settings.sandbox_mount_mode),
            "limits": {
                "memory": settings.sandbox_memory_limit,
                "cpus": settings.sandbox_cpu_limit,
                "pids": max(1, int(settings.sandbox_pids_limit)),
                "tmpfs": settings.sandbox_tmpfs_size,
                "output_bytes": max(1024, int(settings.sandbox_output_limit_bytes or 64_000)),
            },
            "user": settings.sandbox_user,
            "kubernetes": {
                "namespace": self._kubernetes_namespace(settings),
                "service_account": settings.sandbox_kubernetes_service_account,
                "workspace_pvc": settings.sandbox_kubernetes_workspace_pvc,
                "runtime_class": settings.sandbox_kubernetes_runtime_class,
                "egress_rules": list(settings.sandbox_kubernetes_egress_rules),
                "network_policy": True,
                "image_pull_policy": settings.sandbox_kubernetes_image_pull_policy,
                "active_deadline_seconds": max(1, int(settings.sandbox_kubernetes_active_deadline_seconds)),
                "keep_pods": settings.sandbox_kubernetes_keep_pods,
            },
        }

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: str | Path | None,
        workspace_root: str | Path | None,
        timeout_seconds: int,
        env: dict[str, str] | None = None,
    ) -> SandboxCommandResult:
        backend = self.backend
        # A baseline test followed immediately by an equal-size Python patch can
        # otherwise reuse a timestamp-valid __pycache__ entry in the same sandbox.
        env = {"PYTHONDONTWRITEBYTECODE": "1", **(env or {})}
        root = Path(workspace_root or cwd or ".").resolve()
        dependency_path = root / ".researchforge" / "python"
        if dependency_path.is_dir() and dependency_path.resolve().is_relative_to(root):
            if backend == "docker":
                python_path = "/workspace/.researchforge/python"
            elif backend == "kubernetes" and get_settings().sandbox_shared_workspace_root and get_settings().sandbox_kubernetes_workspace_pvc:
                shared = Path(get_settings().sandbox_shared_workspace_root).resolve()
                python_path = "/workspace/" + root.relative_to(shared).as_posix() + "/.researchforge/python"
            elif backend == "kubernetes":
                python_path = "/workspace/.researchforge/python"
            else:
                python_path = str(dependency_path)
            env = {**(env or {}), "PYTHONPATH": python_path + ((os.pathsep if backend == "local" else ":") + env["PYTHONPATH"] if env and env.get("PYTHONPATH") else "")}
        try:
            with sandbox_pool.lease(backend):
                if backend == "local":
                    from app.services.authentication import development_auth
                    if not development_auth():
                        raise SandboxUnavailable("PRODUCTION_SANDBOX_ISOLATION_REQUIRED")
                    return self._run_local(args, cwd=cwd, timeout_seconds=timeout_seconds, env=env)
                if backend == "docker":
                    return self._run_docker(args, cwd=cwd, workspace_root=workspace_root, timeout_seconds=timeout_seconds, env=env)
                if backend == "kubernetes":
                    return self._run_kubernetes(args, cwd=cwd, workspace_root=workspace_root, timeout_seconds=timeout_seconds, env=env)
                raise SandboxUnavailable(f"UNSUPPORTED_SANDBOX_BACKEND:{backend}")
        except SandboxCapacityExceeded as exc:
            raise SandboxUnavailable(str(exc)) from exc

    @staticmethod
    def _run_local(
        args: Sequence[str],
        *,
        cwd: str | Path | None,
        timeout_seconds: int,
        env: dict[str, str] | None = None,
    ) -> SandboxCommandResult:
        return SandboxRunner._run_buffered_process(
            [str(item) for item in args],
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            backend="local",
            env=env,
        )

    def _run_docker(
        self,
        args: Sequence[str],
        *,
        cwd: str | Path | None,
        workspace_root: str | Path | None,
        timeout_seconds: int,
        env: dict[str, str] | None = None,
    ) -> SandboxCommandResult:
        if not workspace_root:
            raise SandboxUnavailable("SANDBOX_REPO_REQUIRED")
        root = Path(workspace_root).expanduser().resolve()
        working_directory = Path(cwd or root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise SandboxUnavailable("SANDBOX_REPO_NOT_FOUND")
        command = self.build_docker_command(args, root, working_directory, env=env)
        try:
            return self._run_buffered_process(
                command,
                cwd=None,
                timeout_seconds=max(1, int(timeout_seconds)),
                backend="docker",
            )
        except FileNotFoundError as exc:
            raise SandboxUnavailable("DOCKER_UNAVAILABLE") from exc

    def _run_kubernetes(
        self,
        args: Sequence[str],
        *,
        cwd: str | Path | None,
        workspace_root: str | Path | None,
        timeout_seconds: int,
        env: dict[str, str] | None = None,
    ) -> SandboxCommandResult:
        if not workspace_root:
            raise SandboxUnavailable("SANDBOX_REPO_REQUIRED")
        root = Path(workspace_root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise SandboxUnavailable("SANDBOX_REPO_NOT_FOUND")
        settings = get_settings()
        if settings.sandbox_kubernetes_workspace_pvc and not settings.sandbox_shared_workspace_root:
            raise SandboxUnavailable("KUBERNETES_WORKSPACE_ROOT_REQUIRED")
        namespace = self._kubernetes_namespace(settings)
        pod_name = f"researchforge-sandbox-{uuid4().hex[:12]}"
        resources = self.build_kubernetes_resources(
            args,
            root,
            cwd or root,
            pod_name=pod_name,
            env=env,
        )
        return self._execute_kubernetes_resources(
            resources,
            pod_name=pod_name,
            timeout_seconds=timeout_seconds,
        )

    def _execute_kubernetes_resources(
        self,
        resources: list[dict[str, object]],
        *,
        pod_name: str,
        timeout_seconds: int,
    ) -> SandboxCommandResult:
        settings = get_settings()
        namespace = self._kubernetes_namespace(settings)
        base = self._kubectl_base_command(settings)
        started = perf_counter()
        output_limit = max(1024, int(settings.sandbox_output_limit_bytes or 64000))
        created = False
        resource_path: Path | None = None
        phase = ""
        exit_code = None
        status_message = ""
        try:
            with TemporaryDirectory(prefix="researchforge-k8s-manifest-") as directory:
                resource_path = Path(directory) / "resources.json"
                resource_path.write_text(
                    json.dumps(
                        {"apiVersion": "v1", "kind": "List", "items": resources},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                applied = subprocess.run(
                    [*base, "apply", "-f", str(resource_path), "-n", namespace],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                created = applied.returncode == 0
                if applied.returncode != 0:
                    stdout, stdout_truncated = self._truncate_text(applied.stdout, output_limit)
                    stderr, stderr_truncated = self._truncate_text(applied.stderr, output_limit)
                    return SandboxCommandResult(applied.returncode, stdout, stderr, int((perf_counter() - started) * 1000), backend="kubernetes", stdout_truncated=stdout_truncated, stderr_truncated=stderr_truncated, output_limit_bytes=output_limit)
                deadline = time.monotonic() + max(1, int(timeout_seconds))
                while time.monotonic() < deadline:
                    current = subprocess.run([*base, "get", "pod", pod_name, "-n", namespace, "-o", "json"], capture_output=True, text=True, timeout=5, check=False)
                    if current.returncode == 0:
                        try:
                            pod = json.loads(current.stdout or "{}")
                        except json.JSONDecodeError:
                            pod = {}
                            status_message = current.stdout.strip()
                        status = pod.get("status") if isinstance(pod, dict) else {}
                        if isinstance(status, dict):
                            phase = str(status.get("phase") or "")
                        exit_code = self._pod_exit_code(pod if isinstance(pod, dict) else {})
                        status_message = self._pod_status_message(pod if isinstance(pod, dict) else {}) or status_message
                        if phase in {"Succeeded", "Failed"}:
                            break
                    else:
                        status_message = current.stderr.strip() or status_message
                    time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
                timed_out = phase not in {"Succeeded", "Failed"}
                logs = subprocess.run([*base, "logs", pod_name, "-n", namespace, "--all-containers=true"], capture_output=True, text=True, timeout=10, check=False)
                stdout, stdout_truncated = self._truncate_text(logs.stdout, output_limit)
                error_parts = [item for item in (logs.stderr.strip(), status_message if phase != "Succeeded" else "", "KUBERNETES_POD_TIMEOUT" if timed_out else "") if item]
                stderr, stderr_truncated = self._truncate_text("\n".join(error_parts), output_limit)
                result_code = None if timed_out else (exit_code if exit_code is not None else (0 if phase == "Succeeded" else 1))
                return SandboxCommandResult(result_code, stdout, stderr, int((perf_counter() - started) * 1000), timed_out=timed_out, backend="kubernetes", stdout_truncated=stdout_truncated, stderr_truncated=stderr_truncated, output_limit_bytes=output_limit)
        except FileNotFoundError as exc:
            raise SandboxUnavailable("KUBECTL_UNAVAILABLE") from exc
        except subprocess.TimeoutExpired:
            return SandboxCommandResult(None, "", "KUBERNETES_COMMAND_TIMEOUT", int((perf_counter() - started) * 1000), timed_out=True, backend="kubernetes", output_limit_bytes=output_limit)
        finally:
            if created and not settings.sandbox_kubernetes_keep_pods:
                try:
                    subprocess.run(
                        [*base, "delete", "pod", pod_name, "-n", namespace, "--ignore-not-found=true"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        check=False,
                    )
                    subprocess.run(
                        [*base, "delete", "networkpolicy", f"{pod_name}-network", "-n", namespace, "--ignore-not-found=true"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass

    @staticmethod
    def _run_buffered_process(
        command: Sequence[str],
        *,
        cwd: str | Path | None,
        timeout_seconds: int,
        backend: str,
        env: dict[str, str] | None = None,
    ) -> SandboxCommandResult:
        started = perf_counter()
        output_limit = max(1024, int(get_settings().sandbox_output_limit_bytes or 64_000))
        with TemporaryDirectory(prefix="researchforge-sandbox-output-", ignore_cleanup_errors=True) as directory:
            stdout_path = Path(directory) / "stdout.log"
            stderr_path = Path(directory) / "stderr.log"
            with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
                process = subprocess.Popen(
                    [str(item) for item in command],
                    cwd=str(cwd) if cwd else None,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    env={**os.environ, **{str(key): str(value) for key, value in (env or {}).items()}},
                    **SandboxRunner._popen_group_kwargs(),
                )
                timed_out = False
                try:
                    returncode = process.wait(timeout=max(1, int(timeout_seconds)))
                except subprocess.TimeoutExpired:
                    timed_out = True
                    SandboxRunner._terminate_process_tree(process)
                    returncode = None
            stdout, stdout_truncated = SandboxRunner._read_tail_text(stdout_path, output_limit)
            stderr, stderr_truncated = SandboxRunner._read_tail_text(stderr_path, output_limit)
            return SandboxCommandResult(
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
                duration_ms=int((perf_counter() - started) * 1000),
                timed_out=timed_out,
                backend=backend,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
                output_limit_bytes=output_limit,
            )

    @staticmethod
    def _popen_group_kwargs() -> dict[str, object]:
        if os.name == "nt":
            return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        return {"start_new_session": True}

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=0.5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return

    @staticmethod
    def _read_tail_text(path: Path, limit_bytes: int) -> tuple[str, bool]:
        if not path.exists():
            return "", False
        size = path.stat().st_size
        truncated = size > limit_bytes
        with path.open("rb") as file:
            if truncated:
                file.seek(-limit_bytes, os.SEEK_END)
            raw = file.read(limit_bytes)
        text = raw.decode("utf-8", errors="replace")
        if truncated:
            text = f"[输出已截断，仅显示最后 {limit_bytes} 字节]\n{text}"
        return text, truncated

    @staticmethod
    def _text(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    @staticmethod
    def _mount_mode(value: str) -> str:
        normalized = str(value or "rw").lower()
        return "ro" if normalized in {"ro", "readonly", "read_only"} else "rw"

    @staticmethod
    def _docker_network(settings: Settings) -> str:
        if not settings.sandbox_network_enabled:
            return "none"
        return settings.sandbox_network_name or "bridge"


    @staticmethod
    def _kubernetes_namespace(settings: Settings) -> str:
        return settings.sandbox_kubernetes_namespace.strip() or "default"

    @staticmethod
    def _kubectl_base_command(settings: Settings) -> list[str]:
        command = ["kubectl"]
        if settings.sandbox_kubernetes_kubeconfig:
            command.extend(["--kubeconfig", settings.sandbox_kubernetes_kubeconfig])
        return command

    @staticmethod
    def _network_label(settings: Settings, backend: str) -> str:
        if backend == "kubernetes":
            return (
                "kubernetes-network-policy-allow-all"
                if settings.sandbox_network_enabled
                else "kubernetes-network-policy-deny-egress"
            )
        return SandboxRunner._docker_network(settings)

    @staticmethod
    def _kubernetes_client_version(raw: str) -> str | None:
        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return raw.strip() or None
        client = data.get("clientVersion") if isinstance(data, dict) else None
        if isinstance(client, dict):
            return str(client.get("gitVersion") or client.get("version") or "") or None
        return raw.strip() or None

    @staticmethod
    def _truncate_text(value: str, limit_bytes: int) -> tuple[str, bool]:
        raw = SandboxRunner._text(value).encode("utf-8", errors="replace")
        truncated = len(raw) > limit_bytes
        if truncated:
            raw = raw[-limit_bytes:]
        text = raw.decode("utf-8", errors="replace")
        if truncated:
            text = f"[输出已截断，仅显示最后 {limit_bytes} 字节]\n{text}"
        return text, truncated

    @staticmethod
    def _pod_exit_code(pod: dict[str, object]) -> int | None:
        status = pod.get("status")
        if not isinstance(status, dict):
            return None
        statuses = status.get("containerStatuses")
        if isinstance(statuses, list):
            for item in statuses:
                if not isinstance(item, dict):
                    continue
                state = item.get("state")
                if not isinstance(state, dict):
                    continue
                terminated = state.get("terminated")
                if isinstance(terminated, dict) and isinstance(terminated.get("exitCode"), int):
                    return int(terminated["exitCode"])
        phase = status.get("phase")
        return 0 if phase == "Succeeded" else 1 if phase == "Failed" else None

    @staticmethod
    def _pod_status_message(pod: dict[str, object]) -> str:
        status = pod.get("status")
        if not isinstance(status, dict):
            return ""
        for key in ("message", "reason"):
            if status.get(key):
                return str(status[key])
        return ""

sandbox_runner = SandboxRunner()













