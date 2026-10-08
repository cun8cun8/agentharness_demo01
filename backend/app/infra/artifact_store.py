from __future__ import annotations

import os
import hashlib
import re
from pathlib import Path
from typing import Any
from app.services.secrets import resolve_secret


class ArtifactBlobStore:
    def __init__(self, settings: Any) -> None:
        self.backend = (getattr(settings, "artifact_store_backend", "inline") or "inline").strip().lower()
        artifact_path = getattr(settings, "artifact_store_path", None)
        self.base_path = Path(artifact_path) if artifact_path else None
        self.bucket = getattr(settings, "artifact_store_bucket", None) or None
        self.prefix = str(getattr(settings, "artifact_store_prefix", "artifacts") or "artifacts").strip("/")
        self.endpoint_url = getattr(settings, "artifact_store_endpoint_url", None) or None
        self.region = getattr(settings, "artifact_store_region", "us-east-1") or "us-east-1"
        self.access_key_id_env = (
            getattr(settings, "artifact_store_access_key_id_env", "MINIO_ROOT_USER")
            or "MINIO_ROOT_USER"
        )
        self.secret_access_key_env = (
            getattr(settings, "artifact_store_secret_access_key_env", "MINIO_ROOT_PASSWORD")
            or "MINIO_ROOT_PASSWORD"
        )
        self.use_ssl = bool(getattr(settings, "artifact_store_use_ssl", False))
        self.auto_create_bucket = bool(
            getattr(settings, "artifact_store_auto_create_bucket", False)
        )
        self.server_side_encryption = getattr(settings, "artifact_store_server_side_encryption", None) or None
        self.kms_key_id = getattr(settings, "artifact_store_kms_key_id", None) or None
        self.workspace_prefix = bool(getattr(settings, "artifact_store_workspace_prefix", True))
        self._s3_client: Any | None = None
        self._credential_fingerprint: str | None = None

    @property
    def enabled(self) -> bool:
        if self.backend == "filesystem":
            return self.base_path is not None
        if self.backend == "s3":
            return bool(self.bucket)
        return False

    def describe(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "enabled": self.enabled,
            "path": str(self.base_path) if self.backend == "filesystem" and self.base_path else None,
            "bucket": self.bucket if self.backend == "s3" else None,
            "prefix": self.prefix if self.backend == "s3" else None,
            "endpoint_url": self.endpoint_url if self.backend == "s3" else None,
            "region": self.region if self.backend == "s3" else None,
            "use_ssl": self.use_ssl if self.backend == "s3" else None,
            "server_side_encryption": self.server_side_encryption if self.backend == "s3" else None,
            "workspace_prefix": self.workspace_prefix if self.backend in {"s3", "filesystem"} else None,
        }

    def probe(self) -> dict[str, object]:
        if self.backend == "filesystem":
            if self.base_path is None:
                return {**self.describe(), "ready": False, "message": "ARTIFACT_STORE_PATH_MISSING"}
            try:
                self.base_path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return {**self.describe(), "ready": False, "message": str(exc)}
            return {**self.describe(), "ready": True, "message": "filesystem artifact store ready"}
        if self.backend == "s3":
            if not self.bucket:
                return {**self.describe(), "ready": False, "message": "ARTIFACT_STORE_BUCKET_MISSING"}
            try:
                self._ensure_s3_bucket()
            except Exception as exc:
                return {**self.describe(), "ready": False, "message": str(exc)}
            return {**self.describe(), "ready": True, "message": "s3 artifact store ready"}
        return {**self.describe(), "ready": False, "message": "INLINE_ARTIFACT_STORE"}

    def should_externalize(self, content: str) -> bool:
        return self.enabled and bool(content)

    def save(self, artifact_id: str, content: str, workspace_id: str | None = None) -> str | None:
        if not self.should_externalize(content):
            return None
        if self.backend == "filesystem":
            return self._save_file(artifact_id, content, workspace_id)
        if self.backend == "s3":
            return self._save_s3(artifact_id, content, workspace_id)
        return None

    def load(self, artifact_id: str, metadata: dict[str, Any] | None = None) -> str | None:
        if self.backend == "filesystem":
            return self._load_file(artifact_id, metadata)
        if self.backend == "s3":
            return self._load_s3(artifact_id, metadata)
        return None

    def _save_file(self, artifact_id: str, content: str, workspace_id: str | None = None) -> str:
        path = self._file_path_for(artifact_id, workspace_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path.resolve())

    def _load_file(self, artifact_id: str, metadata: dict[str, Any] | None) -> str | None:
        path = self._resolve_file_path(artifact_id, metadata)
        if path is None or not path.exists():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    def delete(self, artifact_id: str, metadata: dict[str, Any] | None = None) -> None:
        if self.backend == "filesystem":
            path = self._resolve_file_path(artifact_id, metadata)
            if path is None or self.base_path is None:
                raise ValueError("ARTIFACT_PATH_UNAVAILABLE")
            root = self.base_path.resolve()
            resolved = path.resolve()
            if not resolved.is_relative_to(root) or resolved.name != f"{artifact_id}.txt":
                raise ValueError("ARTIFACT_PATH_OUTSIDE_STORE")
            resolved.unlink(missing_ok=True)
        elif self.backend == "s3":
            bucket, key = self._resolve_s3_location(artifact_id, metadata)
            prefix = self.prefix + "/" if self.prefix else ""
            if bucket != self.bucket or not key.startswith(prefix) or not key.endswith("/" + artifact_id + ".txt") or ".." in key.split("/"):
                raise ValueError("ARTIFACT_KEY_OUTSIDE_STORE")
            self._client().delete_object(Bucket=bucket, Key=key)
        elif self.backend != "inline":
            raise ValueError("ARTIFACT_BACKEND_UNSUPPORTED")

    def _save_s3(self, artifact_id: str, content: str, workspace_id: str | None = None) -> str:
        self._ensure_s3_bucket()
        key = self._s3_key_for(artifact_id, workspace_id)
        request: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": content.encode("utf-8"),
            "ContentType": "text/plain; charset=utf-8",
        }
        if self.server_side_encryption:
            request["ServerSideEncryption"] = self.server_side_encryption
            if self.kms_key_id:
                request["SSEKMSKeyId"] = self.kms_key_id
        self._client().put_object(
            **request,
        )
        return f"s3://{self.bucket}/{key}"

    def _load_s3(self, artifact_id: str, metadata: dict[str, Any] | None) -> str | None:
        if not self.bucket:
            return None
        bucket, key = self._resolve_s3_location(artifact_id, metadata)
        try:
            response = self._client().get_object(Bucket=bucket, Key=key)
            body = response.get("Body")
            raw = body.read() if hasattr(body, "read") else body
        except Exception:
            return None
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw or "")

    def _resolve_file_path(self, artifact_id: str, metadata: dict[str, Any] | None) -> Path | None:
        if metadata:
            content_path = metadata.get("content_path")
            if content_path:
                return Path(str(content_path))
        if self.base_path is None:
            return None
        return self._file_path_for(artifact_id)

    def _resolve_s3_location(self, artifact_id: str, metadata: dict[str, Any] | None) -> tuple[str, str]:
        if metadata:
            content_uri = str(metadata.get("content_uri") or "")
            if content_uri.startswith("s3://"):
                bucket_and_key = content_uri.removeprefix("s3://")
                bucket, _, key = bucket_and_key.partition("/")
                if bucket and key:
                    return bucket, key
            content_key = metadata.get("content_key")
            if content_key:
                return self.bucket or "", str(content_key)
        return self.bucket or "", self._s3_key_for(artifact_id)

    def _file_path_for(self, artifact_id: str, workspace_id: str | None = None) -> Path:
        if self.base_path is None:
            raise RuntimeError("Artifact blob store base path is not configured.")
        first = artifact_id[:2] or "_"
        second = artifact_id[2:4] or "_"
        workspace = self._safe_workspace_segment(workspace_id)
        segments = [workspace] if self.workspace_prefix and workspace else []
        return self.base_path.joinpath(*segments, first, second, f"{artifact_id}.txt")

    def _s3_key_for(self, artifact_id: str, workspace_id: str | None = None) -> str:
        first = artifact_id[:2] or "_"
        second = artifact_id[2:4] or "_"
        workspace = self._safe_workspace_segment(workspace_id)
        segments = [segment for segment in [
            self.prefix,
            workspace if self.workspace_prefix else None,
            first,
            second,
            f"{artifact_id}.txt",
        ] if segment]
        return "/".join(segments)

    @staticmethod
    def _safe_workspace_segment(workspace_id: str | None) -> str | None:
        value = str(workspace_id or "").strip()
        if not value:
            return None
        return re.sub(r"[^A-Za-z0-9_.-]", "_", value)[:120]

    def _ensure_s3_bucket(self) -> None:
        if not self.bucket:
            raise RuntimeError("ARTIFACT_STORE_BUCKET_MISSING")
        client = self._client()
        try:
            client.head_bucket(Bucket=self.bucket)
            return
        except Exception as exc:
            if not self.auto_create_bucket or not self._is_missing_bucket_error(exc):
                raise
        client.create_bucket(Bucket=self.bucket)
        client.head_bucket(Bucket=self.bucket)

    @staticmethod
    def _is_missing_bucket_error(exc: Exception) -> bool:
        response = getattr(exc, "response", None)
        error = response.get("Error", {}) if isinstance(response, dict) else {}
        code = str(error.get("Code", ""))
        return code in {"404", "NoSuchBucket", "NotFound", "NoSuchContainer"}

    def _client(self) -> Any:
        access_key = resolve_secret(self.access_key_id_env)
        secret_key = resolve_secret(self.secret_access_key_env)
        session_token = resolve_secret("RESEARCHFORGE_S3_SESSION_TOKEN")
        fingerprint = hashlib.sha256(repr((access_key, secret_key, session_token)).encode()).hexdigest()
        if self._s3_client is not None and self._credential_fingerprint in {None, fingerprint}:
            return self._s3_client
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError(
                "S3 artifact storage requires boto3. Install backend/requirements.txt first."
            ) from exc
        self._s3_client = boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            region_name=self.region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            aws_session_token=session_token,
            use_ssl=self.use_ssl,
        )
        self._credential_fingerprint = fingerprint
        return self._s3_client
