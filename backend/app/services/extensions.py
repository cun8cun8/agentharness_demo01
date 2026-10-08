from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import yaml

from app.config import get_settings


async def invoke_mcp(config: dict, action: str, arguments: dict) -> dict:
    if action not in {"inspect", "tools/list", "tools/call"}:
        raise ValueError("MCP_ACTION_UNSUPPORTED")
    if action == "tools/call" and config.get("allowed_tools") is not None and arguments.get("name") not in config["allowed_tools"]:
        raise ValueError("MCP_TOOL_NOT_ALLOWED")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client

    timeout = min(120, max(1, int(config.get("timeout_seconds", 30))))
    async with asyncio.timeout(timeout), AsyncExitStack() as stack:
        if config.get("transport") == "stdio":
            command = str(config.get("command") or "")
            if not command:
                raise ValueError("MCP_COMMAND_MISSING")
            env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "TEMP", "TMP") if key in os.environ}
            for target, source in config.get("env_refs", {}).items():
                env[target] = os.environ[source]
            streams = await stack.enter_async_context(stdio_client(StdioServerParameters(
                command=command, args=config.get("args", []), env=env, cwd=config.get("cwd"),
            )))
        else:
            if not get_settings().network_enabled:
                raise ValueError("NETWORK_DISABLED")
            url = str(config.get("url") or "")
            validate_endpoint(url)
            headers = {}
            if config.get("token_env"):
                token = os.environ.get(config["token_env"])
                if not token:
                    raise ValueError("MCP_CREDENTIAL_MISSING")
                headers["Authorization"] = f"Bearer {token}"
            client = await stack.enter_async_context(httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=False))
            streams = await stack.enter_async_context(streamable_http_client(url, http_client=client))
        session = await stack.enter_async_context(ClientSession(streams[0], streams[1], read_timeout_seconds=timedelta(seconds=timeout)))
        await session.initialize()
        if action in {"inspect", "tools/list"}:
            return (await session.list_tools()).model_dump(mode="json", by_alias=True)
        name = str(arguments.get("name") or "")
        result = await session.call_tool(name, arguments=arguments.get("arguments", {}))
        return result.model_dump(mode="json", by_alias=True)


def validate_endpoint(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("EXTENSION_ENDPOINT_INVALID")


def read_skill(path: str) -> dict:
    source = Path(path).resolve()
    if source.is_dir():
        source = source / "SKILL.md"
    if source.suffix.lower() != ".md" or not source.is_file() or source.stat().st_size > 100_000:
        raise ValueError("SKILL_FILE_INVALID")
    text = source.read_text(encoding="utf-8-sig")
    metadata = {}
    if text.startswith("---\n"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            metadata = yaml.safe_load(parts[1]) or {}
            text = parts[2].strip()
    if not isinstance(metadata, dict):
        raise ValueError("SKILL_METADATA_INVALID")
    return {"metadata": metadata, "instructions": text, "path": str(source)}


def deliver_webhook(config: dict, event_type: str, payload: dict, delivery_id: str) -> dict:
    if not get_settings().network_enabled:
        raise ValueError("NETWORK_DISABLED")
    url = str(config.get("url") or config.get("target") or "")
    validate_endpoint(url)
    body = json.dumps({"id": delivery_id, "event_type": event_type, "payload": payload}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    secret = os.environ.get(str(config.get("secret_env") or ""))
    if not secret:
        raise ValueError("HOOK_SIGNING_SECRET_MISSING")
    headers = {
        "Content-Type": "application/json",
        "X-ResearchForge-Delivery": delivery_id,
        "X-ResearchForge-Signature": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(),
    }
    with httpx.Client(timeout=10, follow_redirects=False) as client:
        response = client.post(url, content=body, headers=headers)
        response.raise_for_status()
        return {"status_code": response.status_code, "delivery_id": delivery_id}
