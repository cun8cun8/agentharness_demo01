import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from app.config import get_settings
from app.services.secrets import resolve_secret
from app.domain.schemas import (
    ModelHealthResponse,
    ModelInvokeRequest,
    ModelInvokeResponse,
    ModelProviderConfig,
)


class ModelInvocationError(RuntimeError):
    pass


def model_health(
    model: ModelProviderConfig,
    *,
    verify_connectivity: bool = False,
) -> ModelHealthResponse:
    """Assess a model without exposing credentials or issuing inference requests."""
    provider = model.provider.lower().replace("-", "_")
    if provider == "mock":
        return ModelHealthResponse(
            model_id=model.id,
            model_name=model.model_name,
            provider=model.provider,
            role=model.role,
            healthy=True,
            reason="MOCK_PROVIDER",
            details={
                "source": model.config.get("source", "mock"),
                "strategy_ids": list(model.config.get("strategy_ids") or []),
                "connectivity_checked": False,
            },
        )

    settings = get_settings()
    defaults = {
        "anthropic": ("ANTHROPIC_API_KEY", "https://api.anthropic.com/v1"),
        "claude": ("ANTHROPIC_API_KEY", "https://api.anthropic.com/v1"),
        "gemini": ("GOOGLE_API_KEY", "https://generativelanguage.googleapis.com"),
        "google": ("GOOGLE_API_KEY", "https://generativelanguage.googleapis.com"),
        "google_gemini": ("GOOGLE_API_KEY", "https://generativelanguage.googleapis.com"),
        "openai": (settings.model_gateway_api_key_env, settings.model_gateway_base_url or "https://api.openai.com/v1"),
        "openai_compatible": (settings.model_gateway_api_key_env, settings.model_gateway_base_url or "https://api.openai.com/v1"),
        # LiteLLM Proxy exposes the same OpenAI-compatible contract while
        # owning provider routing, retries, and spend callbacks.
        "litellm": (settings.model_gateway_api_key_env, settings.model_gateway_base_url or "http://127.0.0.1:4000/v1"),
        "qwen": ("DASHSCOPE_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        "dashscope": ("DASHSCOPE_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    }
    if provider not in defaults:
        return ModelHealthResponse(
            model_id=model.id,
            model_name=model.model_name,
            provider=model.provider,
            role=model.role,
            healthy=False,
            reason="MODEL_PROVIDER_UNSUPPORTED",
            details={"source": model.config.get("source", "configured"), "connectivity_checked": False},
        )

    default_key_env, default_base_url = defaults[provider]
    api_key_env = str(model.config.get("api_key_env") or default_key_env)
    base_url = str(model.config.get("base_url") or default_base_url).rstrip("/")
    details: dict[str, object] = {
        "source": model.config.get("source", "configured"),
        "base_url": base_url or None,
        "api_key_env": api_key_env,
        "api_key_present": False,
        "context_window": model.context_window,
        "connectivity_checked": bool(verify_connectivity),
    }
    if not base_url:
        return ModelHealthResponse(model_id=model.id, model_name=model.model_name, provider=model.provider,
                                   role=model.role, healthy=False, reason="BASE_URL_MISSING", details=details)
    try:
        api_key = resolve_secret(api_key_env)
    except Exception:
        return ModelHealthResponse(model_id=model.id, model_name=model.model_name, provider=model.provider,
                                   role=model.role, healthy=False, reason="API_KEY_UNAVAILABLE", details=details)
    if not api_key:
        return ModelHealthResponse(model_id=model.id, model_name=model.model_name, provider=model.provider,
                                   role=model.role, healthy=False, reason="API_KEY_MISSING", details=details)
    details["api_key_present"] = True
    if not verify_connectivity:
        return ModelHealthResponse(model_id=model.id, model_name=model.model_name, provider=model.provider,
                                   role=model.role, healthy=True, reason="CONFIGURED", details=details)
    if not settings.network_enabled:
        return ModelHealthResponse(model_id=model.id, model_name=model.model_name, provider=model.provider,
                                   role=model.role, healthy=False, reason="NETWORK_DISABLED", details=details)

    reachable, reason, status_code = _probe_model_endpoint(provider, base_url, model.model_name, api_key)
    details["probe_status_code"] = status_code
    return ModelHealthResponse(model_id=model.id, model_name=model.model_name, provider=model.provider,
                               role=model.role, healthy=reachable, reason=reason, details=details)


def _probe_model_endpoint(
    provider: str,
    base_url: str,
    model_name: str,
    api_key: str,
) -> tuple[bool, str, int | None]:
    """Probe provider reachability without submitting a completion or recording usage."""
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return False, "MODEL_ENDPOINT_INVALID", None
    if provider in {"gemini", "google", "google_gemini"}:
        endpoint = f"{base_url}/v1beta/models/{urllib.parse.quote(model_name, safe='')}?key={urllib.parse.quote(api_key, safe='')}"
        headers = {}
    elif provider in {"anthropic", "claude"}:
        endpoint = f"{base_url}/models"
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    else:
        endpoint = f"{base_url}/models"
        headers = {"Authorization": f"Bearer {api_key}"}
    request = urllib.request.Request(endpoint, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=min(10, max(1, get_settings().model_gateway_timeout_seconds))) as response:
            return True, "NETWORK_REACHABLE", int(response.status)
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            return False, "MODEL_AUTH_FAILED", exc.code
        # Some providers intentionally have no list-models API. These responses
        # still prove DNS, TLS, routing, and the configured host without inference.
        if exc.code in {404, 405}:
            return True, "NETWORK_REACHABLE", exc.code
        return False, f"MODEL_HTTP_{exc.code}", exc.code
    except (OSError, urllib.error.URLError, ValueError):
        return False, "MODEL_NETWORK_UNAVAILABLE", None


def invoke_configured_model(
    model: ModelProviderConfig,
    request: ModelInvokeRequest,
) -> ModelInvokeResponse:
    from opentelemetry import trace
    started = time.perf_counter()
    with trace.get_tracer(__name__).start_as_current_span("model.invoke") as span:
        span.set_attribute("gen_ai.request.model", model.model_name)
        span.set_attribute("gen_ai.system", model.provider)
        span.set_attribute("researchforge.prompt_version", str(model.config.get("prompt_version") or "v1"))
        try:
            result = _invoke_provider(model, request)
        except Exception as exc:
            span.set_attribute("researchforge.error_type", type(exc).__name__)
            span.set_attribute("researchforge.duration_ms", int((time.perf_counter() - started) * 1000))
            raise
        span.set_attribute("gen_ai.usage.total_tokens", int(result.usage.get("total_tokens") or 0))
        span.set_attribute("researchforge.model.cost", result.estimated_cost)
        span.set_attribute("researchforge.model.attempts", result.attempts)
        span.set_attribute("researchforge.model.fallback_used", result.fallback_used)
        span.set_attribute("researchforge.model.fallback_reason", result.fallback_reason or "")
        span.set_attribute("researchforge.model.version", str(result.raw_response.get("model_version") or model.config.get("model_version") or model.model_name))
        span.set_attribute("researchforge.duration_ms", int((time.perf_counter() - started) * 1000))
        return result


def _invoke_provider(model: ModelProviderConfig, request: ModelInvokeRequest) -> ModelInvokeResponse:
    provider = model.provider.lower().replace("-", "_")
    if provider in {"openai", "openai_compatible", "qwen", "dashscope", "litellm"}:
        return _invoke_openai_compatible(model, request)
    if provider in {"anthropic", "claude"}:
        return _invoke_anthropic(model, request)
    if provider in {"gemini", "google", "google_gemini"}:
        return _invoke_gemini(model, request)
    if provider == "mock":
        if not get_settings().allow_mock_models:
            raise ModelInvocationError("MOCK_MODELS_DISABLED")
        return _invoke_mock(model, request)
    raise ModelInvocationError("MODEL_PROVIDER_UNSUPPORTED")


def _invoke_mock(model: ModelProviderConfig, request: ModelInvokeRequest) -> ModelInvokeResponse:
    prompt_preview = " ".join(request.prompt.split())[:180]
    system_preview = " ".join((request.system_prompt or "").split())[:120]
    output_parts = [
        f"模型 {model.model_name} 已接收任务。",
        f"任务类型：{request.task_type.value}。",
        f"用户输入摘要：{prompt_preview or '空'}",
    ]
    if system_preview:
        output_parts.append(f"系统约束摘要：{system_preview}")
    output_parts.append("这是本地 mock 响应；配置 OpenAI-compatible provider 和密钥后可调用真实模型。")
    usage = _estimate_usage(request)
    return ModelInvokeResponse(
        provider=model.provider,
        model_name=model.model_name,
        output_text="\n".join(output_parts),
        usage=usage,
        estimated_cost=_estimated_cost(model, usage),
        fallback_used=True,
        attempts=1,
        fallback_reason="MOCK_PROVIDER",
    )


def _invoke_openai_compatible(
    model: ModelProviderConfig,
    request: ModelInvokeRequest,
) -> ModelInvokeResponse:
    settings = get_settings()
    provider = model.provider.lower().replace("-", "_")
    default_base_url = (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
        if provider in {"qwen", "dashscope"}
        else "http://127.0.0.1:4000/v1"
        if provider == "litellm"
        else "https://api.openai.com/v1"
    )
    default_key_env = "DASHSCOPE_API_KEY" if provider in {"qwen", "dashscope"} else settings.model_gateway_api_key_env
    base_url = str(
        model.config.get("base_url")
        or settings.model_gateway_base_url
        or default_base_url
    ).rstrip("/")
    api_key_env = str(model.config.get("api_key_env") or default_key_env)
    api_key = resolve_secret(api_key_env)
    if not api_key:
        if not settings.allow_mock_models:
            raise ModelInvocationError("MODEL_API_KEY_MISSING")
        response = _invoke_mock(model, request)
        response.output_text = f"未配置环境变量 {api_key_env}，已回退本地 mock。\n\n{response.output_text}"
        response.fallback_used = True
        response.fallback_reason = "API_KEY_MISSING"
        return response

    messages = []
    if request.system_prompt:
        messages.append({"role": "system", "content": request.system_prompt})
    messages.append({"role": "user", "content": request.prompt})
    payload = {
        "model": model.model_name,
        "messages": messages,
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
    }
    raw: dict[str, object] | None = None
    last_error: Exception | None = None
    max_retries = _int_config(model.config.get("max_retries"), settings.model_gateway_max_retries, minimum=0)
    max_attempts = max_retries + 1
    attempts = 0
    # Use the maintained OpenAI-compatible client for OpenAI, Qwen, DashScope,
    # LiteLLM and vLLM endpoints. Provider-specific code remains limited to
    # translating our domain request and recording platform usage.
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - requirements install guard
        raise ModelInvocationError("OPENAI_CLIENT_NOT_INSTALLED") from exc

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=max(1, settings.model_gateway_timeout_seconds),
        max_retries=0,
    )
    for attempt in range(1, max_attempts + 1):
        attempts = attempt
        try:
            response = client.chat.completions.create(**payload)
            raw = response.model_dump() if hasattr(response, "model_dump") else dict(response)
            _validate_openai_response(raw)
            break
        except Exception as exc:
            last_error = exc
            if attempt < max_attempts and _should_retry(exc):
                time.sleep(max(0.0, settings.model_gateway_retry_backoff_seconds) * attempt)
            elif attempt < max_attempts:
                break
    if raw is None or last_error is not None and not _has_valid_choice(raw):
        close = getattr(client, "close", None)
        if close is not None:
            close()
        if not settings.allow_mock_models:
            raise ModelInvocationError("MODEL_PROVIDER_UNAVAILABLE") from last_error
        fallback = _invoke_mock(model, request)
        fallback.output_text = f"真实模型调用失败，已回退本地 mock：{last_error}\n\n{fallback.output_text}"
        fallback.fallback_used = True
        fallback.attempts = attempts
        fallback.fallback_reason = type(last_error).__name__ if last_error else "INVALID_RESPONSE"
        fallback.raw_response = {"attempts": attempts, "error": str(last_error)}
        return fallback

    choice = (raw.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    usage = raw.get("usage") or _estimate_usage(request)
    close = getattr(client, "close", None)
    if close is not None:
        close()
    return ModelInvokeResponse(
        provider=model.provider,
        model_name=model.model_name,
        output_text=str(message.get("content") or ""),
        finish_reason=str(choice.get("finish_reason") or "stop"),
        usage=usage,
        estimated_cost=_estimated_cost(model, usage),
        fallback_used=False,
        attempts=attempts,
        raw_response={"id": raw.get("id"), "created": raw.get("created"), "attempts": attempts},
    )


def _invoke_anthropic(model: ModelProviderConfig, request: ModelInvokeRequest) -> ModelInvokeResponse:
    settings = get_settings()
    base_url = str(model.config.get("base_url") or "https://api.anthropic.com").rstrip("/")
    endpoint = str(model.config.get("endpoint") or (f"{base_url}/messages" if base_url.endswith("/v1") else f"{base_url}/v1/messages"))
    api_key_env = str(model.config.get("api_key_env") or "ANTHROPIC_API_KEY")
    api_key = resolve_secret(api_key_env)
    if not api_key:
        return _native_fallback(model, request, "API_KEY_MISSING", f"未配置环境变量 {api_key_env}")
    messages = [{"role": "user", "content": request.prompt}]
    payload: dict[str, object] = {"model": model.model_name, "max_tokens": request.max_tokens, "temperature": request.temperature, "messages": messages}
    if request.system_prompt:
        payload["system"] = request.system_prompt
    try:
        raw, attempts = _post_json_with_retries(
            endpoint,
            payload,
            {"x-api-key": api_key, "anthropic-version": str(model.config.get("anthropic_version") or "2023-06-01")},
            model,
        )
        content = raw.get("content") or []
        output = "".join(str(item.get("text") or "") for item in content if isinstance(item, dict) and item.get("type") == "text")
        if not output:
            raise ValueError("MODEL_RESPONSE_MISSING_CONTENT")
        usage_raw = raw.get("usage") or {}
        usage = {"prompt_tokens": int(usage_raw.get("input_tokens") or 0), "completion_tokens": int(usage_raw.get("output_tokens") or 0)}
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        return ModelInvokeResponse(provider=model.provider, model_name=model.model_name, output_text=output,
                                   finish_reason=str(raw.get("stop_reason") or "stop"), usage=usage,
                                   estimated_cost=_estimated_cost(model, usage), attempts=attempts,
                                   raw_response={"id": raw.get("id"), "type": raw.get("type"), "attempts": attempts})
    except Exception as exc:
        return _native_failure(model, request, exc)


def _invoke_gemini(model: ModelProviderConfig, request: ModelInvokeRequest) -> ModelInvokeResponse:
    settings = get_settings()
    base_url = str(model.config.get("base_url") or "https://generativelanguage.googleapis.com").rstrip("/")
    api_key_env = str(model.config.get("api_key_env") or "GOOGLE_API_KEY")
    api_key = resolve_secret(api_key_env)
    if not api_key:
        return _native_fallback(model, request, "API_KEY_MISSING", f"未配置环境变量 {api_key_env}")
    if base_url.endswith("/v1beta"):
        endpoint = f"{base_url}/models/{urllib.parse.quote(model.model_name, safe='')}:generateContent"
    else:
        endpoint = f"{base_url}/v1beta/models/{urllib.parse.quote(model.model_name, safe='')}:generateContent"
    endpoint += "?key=" + urllib.parse.quote(api_key, safe="")
    payload: dict[str, object] = {
        "contents": [{"role": "user", "parts": [{"text": request.prompt}]}],
        "generationConfig": {"maxOutputTokens": request.max_tokens, "temperature": request.temperature},
    }
    if request.system_prompt:
        payload["systemInstruction"] = {"parts": [{"text": request.system_prompt}]}
    try:
        raw, attempts = _post_json_with_retries(endpoint, payload, {}, model)
        candidates = raw.get("candidates") or []
        first = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
        parts = ((first.get("content") or {}).get("parts") or []) if isinstance(first, dict) else []
        output = "".join(str(item.get("text") or "") for item in parts if isinstance(item, dict))
        if not output:
            raise ValueError("MODEL_RESPONSE_MISSING_CONTENT")
        usage_raw = raw.get("usageMetadata") or {}
        usage = {"prompt_tokens": int(usage_raw.get("promptTokenCount") or 0), "completion_tokens": int(usage_raw.get("candidatesTokenCount") or 0), "total_tokens": int(usage_raw.get("totalTokenCount") or 0)}
        if not usage["total_tokens"]:
            usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        return ModelInvokeResponse(provider=model.provider, model_name=model.model_name, output_text=output,
                                   finish_reason=str(first.get("finishReason") or "STOP"), usage=usage,
                                   estimated_cost=_estimated_cost(model, usage), attempts=attempts,
                                   raw_response={"model_version": raw.get("modelVersion"), "attempts": attempts})
    except Exception as exc:
        return _native_failure(model, request, exc)


def _post_json_with_retries(url: str, payload: dict[str, object], extra_headers: dict[str, str], model: ModelProviderConfig) -> tuple[dict[str, object], int]:
    settings = get_settings()
    http_request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **extra_headers},
        method="POST",
    )
    last_error: Exception | None = None
    for attempt in range(1, _int_config(model.config.get("max_retries"), settings.model_gateway_max_retries) + 2):
        try:
            with urllib.request.urlopen(http_request, timeout=settings.model_gateway_timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("MODEL_RESPONSE_INVALID")
            return raw, attempt
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            if attempt <= _int_config(model.config.get("max_retries"), settings.model_gateway_max_retries) and _should_retry(exc):
                time.sleep(max(0.0, settings.model_gateway_retry_backoff_seconds) * attempt)
                continue
            break
    raise ModelInvocationError("MODEL_PROVIDER_UNAVAILABLE") from last_error


def _native_fallback(model: ModelProviderConfig, request: ModelInvokeRequest, reason: str, message: str) -> ModelInvokeResponse:
    if not get_settings().allow_mock_models:
        raise ModelInvocationError(reason)
    response = _invoke_mock(model, request)
    response.output_text = message + "；已回退本地 mock。\n\n" + response.output_text
    response.fallback_used = True
    response.fallback_reason = reason
    return response


def _native_failure(model: ModelProviderConfig, request: ModelInvokeRequest, error: Exception) -> ModelInvokeResponse:
    if not get_settings().allow_mock_models:
        raise error
    response = _invoke_mock(model, request)
    response.output_text = f"真实模型调用失败，已回退本地 mock：{type(error).__name__}\n\n" + response.output_text
    response.fallback_used = True
    response.fallback_reason = type(error).__name__
    response.raw_response = {"error": type(error).__name__}
    return response


def _estimate_usage(request: ModelInvokeRequest) -> dict[str, int]:
    prompt_chars = len(request.prompt) + len(request.system_prompt or "")
    prompt_tokens = max(1, prompt_chars // 4)
    completion_tokens = max(1, min(request.max_tokens, 128))
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def _estimated_cost(model: ModelProviderConfig, usage: dict[str, object]) -> float:
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    if prompt or completion:
        input_rate = float(model.config.get("input_cost_per_1k_tokens", model.cost_per_1k_tokens))
        output_rate = float(model.config.get("output_cost_per_1k_tokens", model.cost_per_1k_tokens))
        return round((prompt * input_rate + completion * output_rate) / 1000, 6)
    return round(int(usage.get("total_tokens") or 0) / 1000 * model.cost_per_1k_tokens, 6)


def _validate_openai_response(raw: object) -> None:
    if not isinstance(raw, dict):
        raise ValueError("MODEL_RESPONSE_INVALID")
    if not _has_valid_choice(raw):
        raise ValueError("MODEL_RESPONSE_MISSING_CHOICE")


def _has_valid_choice(raw: object) -> bool:
    if not isinstance(raw, dict):
        return False
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        return False
    first = choices[0]
    if not isinstance(first, dict):
        return False
    message = first.get("message")
    return isinstance(message, dict) and "content" in message


def _int_config(value: object, default: int, *, minimum: int = 0) -> int:
    try:
        return max(minimum, int(value if value is not None else default))
    except (TypeError, ValueError):
        return max(minimum, default)


def _should_retry(error: Exception) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code == 429 or error.code >= 500
    # The official OpenAI client exposes provider-neutral retryable errors.
    # Keep the import optional so mock-only/local installs remain usable.
    try:
        from openai import APIConnectionError, APIStatusError, RateLimitError
        if isinstance(error, (APIConnectionError, RateLimitError)):
            return True
        if isinstance(error, APIStatusError):
            return int(getattr(error, "status_code", 0) or 0) in {408, 409, 429} or int(getattr(error, "status_code", 0) or 0) >= 500
    except ImportError:  # pragma: no cover - optional compatibility path
        pass
    if isinstance(error, ValueError):
        return str(error) in {"MODEL_RESPONSE_INVALID", "MODEL_RESPONSE_MISSING_CHOICE"}
    return True
