"""Helpers for the local Ollama-backed runtime."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Mapping
from urllib.parse import urlparse

from ese.config import resolve_role_model

DEFAULT_LOCAL_BASE_URL = "http://localhost:11434/v1"
_LOCAL_RUNTIME_READY_CACHE_KEY = "_ese_local_runtime_ready"


class LocalRuntimeError(RuntimeError):
    """Raised when the local Ollama runtime is unavailable or misconfigured."""


def _runtime_ready_cache(cfg: Mapping[str, Any]) -> set[str] | None:
    if not isinstance(cfg, dict):
        return None

    cache = cfg.get(_LOCAL_RUNTIME_READY_CACHE_KEY)
    if isinstance(cache, set):
        return cache

    if cache is None:
        created: set[str] = set()
        cfg[_LOCAL_RUNTIME_READY_CACHE_KEY] = created
        return created

    return None


def local_runtime_selected(cfg: Mapping[str, Any]) -> bool:
    runtime_cfg = cfg.get("runtime")
    if isinstance(runtime_cfg, Mapping):
        adapter = str(runtime_cfg.get("adapter") or "").strip().lower()
        if adapter == "local":
            return True

    provider_cfg = cfg.get("provider")
    if isinstance(provider_cfg, Mapping):
        provider_name = str(provider_cfg.get("name") or "").strip().lower()
        if provider_name == "local":
            return True
    return False


def local_base_url(cfg: Mapping[str, Any]) -> str:
    runtime_cfg = cfg.get("runtime")
    provider_cfg = cfg.get("provider")
    runtime_local: Mapping[str, Any] = {}
    if isinstance(runtime_cfg, Mapping):
        nested = runtime_cfg.get("local")
        if isinstance(nested, Mapping):
            runtime_local = nested

    if isinstance(runtime_local.get("base_url"), str) and str(runtime_local.get("base_url")).strip():
        return str(runtime_local.get("base_url")).strip().rstrip("/")

    if isinstance(provider_cfg, Mapping):
        raw = provider_cfg.get("base_url")
        if isinstance(raw, str) and raw.strip():
            return raw.strip().rstrip("/")

    return DEFAULT_LOCAL_BASE_URL


def ollama_installed() -> bool:
    return shutil.which("ollama") is not None


def _ollama_probe_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]
    probe_path = f"{path}/api/tags" if path else "/api/tags"
    return parsed._replace(path=probe_path, params="", query="", fragment="").geturl()


def ollama_running(base_url: str, *, timeout_seconds: float = 2.0) -> bool:
    request = urllib.request.Request(_ollama_probe_url(base_url), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            return 200 <= getattr(response, "status", 200) < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def start_ollama_server(*, detach: bool = True) -> None:
    ollama_path = shutil.which("ollama")
    if not ollama_path:
        raise LocalRuntimeError(
            "Ollama is not installed. Install it from https://ollama.com/download or choose a hosted provider instead.",
        )

    if detach:
        subprocess.Popen(  # noqa: S603
            [ollama_path, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        return

    subprocess.run([ollama_path, "serve"], check=True)  # noqa: S603


def wait_for_ollama(base_url: str, *, timeout_seconds: float = 12.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if ollama_running(base_url):
            return True
        time.sleep(0.5)
    return False


def fetch_ollama_models(base_url: str) -> set[str]:
    request = urllib.request.Request(_ollama_probe_url(base_url), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=3.0) as response:  # noqa: S310
            parsed = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as err:
        raise LocalRuntimeError(f"Could not read available Ollama models from {base_url}") from err

    if not isinstance(parsed, Mapping):
        return set()

    models = parsed.get("models")
    if not isinstance(models, list):
        return set()

    available: set[str] = set()
    for item in models:
        if not isinstance(item, Mapping):
            continue
        for key in ("model", "name"):
            raw = item.get(key)
            if isinstance(raw, str) and raw.strip():
                available.add(raw.strip())
    return available


def required_local_models(cfg: Mapping[str, Any]) -> list[str]:
    roles_cfg = cfg.get("roles")
    if not isinstance(roles_cfg, Mapping):
        return []

    required: list[str] = []
    seen: set[str] = set()
    for role in roles_cfg.keys():
        model_ref = resolve_role_model(dict(cfg), str(role))
        provider, _, model_name = model_ref.partition(":")
        if provider.strip().lower() != "local":
            continue
        clean_model = model_name.strip()
        if clean_model and clean_model not in seen:
            seen.add(clean_model)
            required.append(clean_model)
    return required


def ensure_local_runtime_ready(
    cfg: Mapping[str, Any],
    *,
    auto_start: bool = True,
    require_models: bool = True,
) -> None:
    if not local_runtime_selected(cfg):
        return

    base_url = local_base_url(cfg)
    required = required_local_models(cfg) if require_models else []
    cache_key = "|".join(
        [
            base_url,
            "auto" if auto_start else "manual",
            "models" if require_models else "no-models",
            ",".join(required),
        ],
    )
    ready_cache = _runtime_ready_cache(cfg)
    if ready_cache is not None and cache_key in ready_cache:
        return

    if not ollama_running(base_url):
        if not ollama_installed():
            raise LocalRuntimeError(
                "Local runtime selected but Ollama is not installed. Install it from https://ollama.com/download "
                "or choose a hosted provider instead.",
            )

        if not auto_start:
            raise LocalRuntimeError(
                f"Local runtime selected but Ollama is not running at {base_url}. Start it with `ollama serve`.",
            )

        start_ollama_server(detach=True)
        if not wait_for_ollama(base_url):
            raise LocalRuntimeError(
                f"Ollama is installed but did not start successfully at {base_url}. "
                "Try `ollama serve` manually and re-run ESE.",
            )

    if not require_models:
        if ready_cache is not None:
            ready_cache.add(cache_key)
        return

    if not required:
        if ready_cache is not None:
            ready_cache.add(cache_key)
        return

    available = fetch_ollama_models(base_url)
    missing = [model for model in required if model not in available]
    if missing:
        command = " && ".join(f"ollama pull {model}" for model in missing)
        raise LocalRuntimeError(
            "Ollama is running but required local models are missing: "
            f"{', '.join(missing)}. Pull them first: {command}",
        )
    if ready_cache is not None:
        ready_cache.add(cache_key)
