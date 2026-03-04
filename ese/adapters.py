"""Built-in runtime adapters for ESE role execution."""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Mapping

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_CUSTOM_API_KEY_ENV = "CUSTOM_API_KEY"


class AdapterExecutionError(RuntimeError):
    """Raised when a runtime adapter cannot execute successfully."""


def dry_run_adapter(
    *,
    role: str,
    model: str,
    prompt: str,
    context: Mapping[str, str],
    cfg: Mapping[str, Any],
) -> str:
    """Return deterministic placeholder output without external model calls."""
    snippet = prompt[:400].strip()
    lines = [
        f"# {role}",
        "",
        f"Model: {model}",
        "Adapter: dry-run",
        "",
        "Prompt excerpt:",
        snippet or "(empty prompt)",
    ]
    if context:
        lines.extend(["", "Context keys:", ", ".join(sorted(context.keys()))])
    return "\n".join(lines) + "\n"


def _parse_provider_model(model: str) -> tuple[str, str]:
    if ":" in model:
        provider, model_name = model.split(":", 1)
        return provider.strip().lower(), model_name.strip()
    return "unknown", model.strip()


def _runtime_number(
    runtime_cfg: Mapping[str, Any],
    name: str,
    default: float,
    *,
    allow_zero: bool = False,
) -> float:
    raw = runtime_cfg.get(name, default)
    try:
        value = float(raw)
    except (TypeError, ValueError) as err:
        raise AdapterExecutionError(f"runtime.{name} must be numeric") from err
    if value < 0:
        comparator = ">= 0" if allow_zero else "> 0"
        raise AdapterExecutionError(f"runtime.{name} must be {comparator}")
    if not allow_zero and value == 0:
        raise AdapterExecutionError(f"runtime.{name} must be > 0")
    return value


def _provider_cfg(cfg: Mapping[str, Any]) -> Mapping[str, Any]:
    provider = cfg.get("provider")
    if isinstance(provider, Mapping):
        return provider
    return {}


def _runtime_cfg(cfg: Mapping[str, Any]) -> Mapping[str, Any]:
    runtime = cfg.get("runtime")
    if isinstance(runtime, Mapping):
        return runtime
    return {}


def _runtime_openai_cfg(cfg: Mapping[str, Any]) -> Mapping[str, Any]:
    runtime = _runtime_cfg(cfg)
    openai_cfg = runtime.get("openai")
    if isinstance(openai_cfg, Mapping):
        return openai_cfg
    return {}


def _runtime_custom_api_cfg(cfg: Mapping[str, Any]) -> Mapping[str, Any]:
    runtime = _runtime_cfg(cfg)
    custom_api_cfg = runtime.get("custom_api")
    if isinstance(custom_api_cfg, Mapping):
        return custom_api_cfg
    return {}


def _openai_base_url(cfg: Mapping[str, Any]) -> str:
    provider_cfg = _provider_cfg(cfg)
    openai_cfg = _runtime_openai_cfg(cfg)

    base_url = (
        openai_cfg.get("base_url")
        or provider_cfg.get("base_url")
        or DEFAULT_OPENAI_BASE_URL
    )
    if not isinstance(base_url, str) or not base_url.strip():
        raise AdapterExecutionError("OpenAI base URL must be a non-empty string")
    return base_url.rstrip("/")


def _custom_api_base_url(cfg: Mapping[str, Any]) -> str:
    provider_cfg = _provider_cfg(cfg)
    custom_api_cfg = _runtime_custom_api_cfg(cfg)
    base_url = custom_api_cfg.get("base_url") or provider_cfg.get("base_url")
    if not isinstance(base_url, str) or not base_url.strip():
        raise AdapterExecutionError(
            "Custom API base URL is required. Set provider.base_url or runtime.custom_api.base_url.",
        )
    return base_url.rstrip("/")


def _api_key_from_env(cfg: Mapping[str, Any], *, default_env: str, adapter_name: str) -> str:
    provider_cfg = _provider_cfg(cfg)
    api_key_env = provider_cfg.get("api_key_env") or default_env
    if not isinstance(api_key_env, str) or not api_key_env.strip():
        raise AdapterExecutionError("provider.api_key_env must be a non-empty string")
    api_key_env = api_key_env.strip()

    api_key = os.getenv(api_key_env)
    if not api_key:
        raise AdapterExecutionError(
            f"Missing API key in env var '{api_key_env}' for {adapter_name} adapter",
        )
    return api_key


def _openai_api_key(cfg: Mapping[str, Any]) -> str:
    return _api_key_from_env(cfg, default_env="OPENAI_API_KEY", adapter_name="OpenAI")


def _custom_api_key(cfg: Mapping[str, Any]) -> str:
    return _api_key_from_env(
        cfg,
        default_env=DEFAULT_CUSTOM_API_KEY_ENV,
        adapter_name="custom_api",
    )


def _openai_payload(
    *,
    role: str,
    model_name: str,
    prompt: str,
    context: Mapping[str, str],
    cfg: Mapping[str, Any],
) -> dict[str, Any]:
    runtime_cfg = _runtime_cfg(cfg)
    context_lines = [f"{name}: {value}" for name, value in sorted(context.items()) if value]
    context_text = ""
    if context_lines:
        context_text = "\n\nUpstream context:\n" + "\n\n".join(context_lines)

    payload: dict[str, Any] = {
        "model": model_name,
        "instructions": (
            f"You are the {role} role in an ensemble software engineering pipeline. "
            "Respond in concise Markdown focused on actionable output."
        ),
        "input": prompt + context_text,
    }

    max_output_tokens = runtime_cfg.get("max_output_tokens")
    if max_output_tokens is not None:
        try:
            token_limit = int(max_output_tokens)
        except (TypeError, ValueError) as err:
            raise AdapterExecutionError("runtime.max_output_tokens must be an integer") from err
        if token_limit <= 0:
            raise AdapterExecutionError("runtime.max_output_tokens must be > 0")
        payload["max_output_tokens"] = token_limit

    return payload


def _extract_openai_text(data: Mapping[str, Any]) -> str:
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    output = data.get("output")
    texts: list[str] = []
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, Mapping):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, Mapping):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())

    if texts:
        return "\n\n".join(texts)
    raise AdapterExecutionError("OpenAI response did not contain text output")


def _is_retryable_status(status_code: int) -> bool:
    return status_code in {408, 409, 429} or status_code >= 500


def _truncate_for_error(text: str, limit: int = 500) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def _execute_responses_request(
    *,
    url: str,
    api_key: str,
    payload: Mapping[str, Any],
    timeout_seconds: float,
    max_retries: int,
    retry_backoff_seconds: float,
    auth_error_message: str,
    provider_name: str,
) -> str:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    last_error: str | None = None
    attempts = max_retries + 1
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                response_text = response.read().decode("utf-8")
                parsed = json.loads(response_text)
                if not isinstance(parsed, Mapping):
                    raise AdapterExecutionError(f"{provider_name} response JSON must be an object")
                return _extract_openai_text(parsed)
        except urllib.error.HTTPError as err:
            response_body = err.read().decode("utf-8", errors="replace")
            status = err.code
            if status in {401, 403}:
                raise AdapterExecutionError(auth_error_message) from err

            last_error = f"HTTP {status}: {_truncate_for_error(response_body)}"
            if attempt < attempts and _is_retryable_status(status):
                time.sleep(retry_backoff_seconds * attempt)
                continue

            raise AdapterExecutionError(f"{provider_name} request failed ({last_error})") from err
        except (urllib.error.URLError, TimeoutError, socket.timeout) as err:
            last_error = str(err)
            if attempt < attempts:
                time.sleep(retry_backoff_seconds * attempt)
                continue
            raise AdapterExecutionError(f"{provider_name} request failed after retries: {last_error}") from err
        except json.JSONDecodeError as err:
            raise AdapterExecutionError(f"{provider_name} response was not valid JSON") from err

    raise AdapterExecutionError(
        f"{provider_name} request failed after retries: {last_error or 'unknown error'}",
    )


def openai_adapter(
    *,
    role: str,
    model: str,
    prompt: str,
    context: Mapping[str, str],
    cfg: Mapping[str, Any],
) -> str:
    """Execute role prompt using the OpenAI Responses API."""
    provider_name, model_name = _parse_provider_model(model)
    if provider_name != "openai":
        raise AdapterExecutionError(
            f"OpenAI adapter requires openai:* model refs, received '{model}'",
        )
    if not model_name:
        raise AdapterExecutionError("Model reference is missing model name")

    runtime_cfg = _runtime_cfg(cfg)
    timeout_seconds = _runtime_number(runtime_cfg, "timeout_seconds", 60.0)
    max_retries = int(_runtime_number(runtime_cfg, "max_retries", 2, allow_zero=True))
    retry_backoff_seconds = _runtime_number(runtime_cfg, "retry_backoff_seconds", 1.0)

    payload = _openai_payload(
        role=role,
        model_name=model_name,
        prompt=prompt,
        context=context,
        cfg=cfg,
    )
    base_url = _openai_base_url(cfg)
    api_key = _openai_api_key(cfg)
    url = f"{base_url}/responses"

    return _execute_responses_request(
        url=url,
        api_key=api_key,
        payload=payload,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        auth_error_message="OpenAI authentication failed. Check provider.api_key_env and token scope.",
        provider_name="OpenAI",
    )


def custom_api_adapter(
    *,
    role: str,
    model: str,
    prompt: str,
    context: Mapping[str, str],
    cfg: Mapping[str, Any],
) -> str:
    """Execute role prompt against a custom Responses-compatible API gateway."""
    provider_name, model_name = _parse_provider_model(model)
    configured_provider = str((_provider_cfg(cfg).get("name") or "")).strip().lower()
    if not configured_provider:
        raise AdapterExecutionError("provider.name must be a non-empty string for custom_api adapter")
    if configured_provider == "openai":
        raise AdapterExecutionError("custom_api adapter cannot be used with provider.name='openai'")
    if provider_name in {"", "unknown"}:
        raise AdapterExecutionError("Custom API role model must include provider and model id")
    if provider_name != configured_provider:
        raise AdapterExecutionError(
            f"Role model provider '{provider_name}' does not match configured provider '{configured_provider}'",
        )
    if not model_name:
        raise AdapterExecutionError("Custom API role model is missing model id")

    runtime_cfg = _runtime_cfg(cfg)
    timeout_seconds = _runtime_number(runtime_cfg, "timeout_seconds", 60.0)
    max_retries = int(_runtime_number(runtime_cfg, "max_retries", 2, allow_zero=True))
    retry_backoff_seconds = _runtime_number(runtime_cfg, "retry_backoff_seconds", 1.0)

    payload = _openai_payload(
        role=role,
        model_name=model_name,
        prompt=prompt,
        context=context,
        cfg=cfg,
    )
    base_url = _custom_api_base_url(cfg)
    api_key = _custom_api_key(cfg)
    url = f"{base_url}/responses"

    return _execute_responses_request(
        url=url,
        api_key=api_key,
        payload=payload,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        auth_error_message="Custom API authentication failed. Check provider.api_key_env and token scope.",
        provider_name="Custom API",
    )


BUILTIN_ADAPTERS = {
    "dry-run": dry_run_adapter,
    "openai": openai_adapter,
    "custom_api": custom_api_adapter,
}
