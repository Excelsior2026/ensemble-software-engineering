"""Shared provider/runtime capability metadata and helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

BUILTIN_RUNTIME_ADAPTER_CHOICES = ("dry-run", "openai", "local", "custom_api")
BUILTIN_RUNTIME_ADAPTERS = frozenset(BUILTIN_RUNTIME_ADAPTER_CHOICES)
BUILTIN_LIVE_RUNTIME_ADAPTERS = frozenset(adapter for adapter in BUILTIN_RUNTIME_ADAPTER_CHOICES if adapter != "dry-run")
BUILTIN_RUNTIME_ADAPTERS_TEXT = "{'dry-run', 'openai', 'local', 'custom_api'}"
PROVIDER_CHOICES = (
    "openai",
    "anthropic",
    "google",
    "xai",
    "openrouter",
    "huggingface",
    "local",
    "custom_api",
)


@dataclass(frozen=True)
class ProviderRuntimeCapability:
    name: str
    default_api_key_env: str
    supports_builtin_live: bool
    builtin_runtime_adapter: str | None
    live_title: str
    demo_title: str
    include_in_env_detection: bool = True
    prefer_live_when_selected: bool = False


PROVIDER_RUNTIME_CAPABILITIES = {
    "openai": ProviderRuntimeCapability(
        name="openai",
        default_api_key_env="OPENAI_API_KEY",
        supports_builtin_live=True,
        builtin_runtime_adapter="openai",
        live_title="openai - built-in live adapter",
        demo_title="openai - demo or built-in live adapter",
    ),
    "anthropic": ProviderRuntimeCapability(
        name="anthropic",
        default_api_key_env="ANTHROPIC_API_KEY",
        supports_builtin_live=False,
        builtin_runtime_adapter=None,
        live_title="anthropic - requires custom module adapter",
        demo_title="anthropic - demo only unless you bring a custom adapter",
    ),
    "google": ProviderRuntimeCapability(
        name="google",
        default_api_key_env="GOOGLE_API_KEY",
        supports_builtin_live=False,
        builtin_runtime_adapter=None,
        live_title="google - requires custom module adapter",
        demo_title="google - demo only unless you bring a custom adapter",
    ),
    "xai": ProviderRuntimeCapability(
        name="xai",
        default_api_key_env="XAI_API_KEY",
        supports_builtin_live=False,
        builtin_runtime_adapter=None,
        live_title="xai - requires custom module adapter",
        demo_title="xai - demo only unless you bring a custom adapter",
    ),
    "openrouter": ProviderRuntimeCapability(
        name="openrouter",
        default_api_key_env="OPENROUTER_API_KEY",
        supports_builtin_live=False,
        builtin_runtime_adapter=None,
        live_title="openrouter - requires custom module adapter",
        demo_title="openrouter - demo only unless you bring a custom adapter",
    ),
    "huggingface": ProviderRuntimeCapability(
        name="huggingface",
        default_api_key_env="HF_TOKEN",
        supports_builtin_live=False,
        builtin_runtime_adapter=None,
        live_title="huggingface - requires custom module adapter",
        demo_title="huggingface - demo only unless you bring a custom adapter",
    ),
    "local": ProviderRuntimeCapability(
        name="local",
        default_api_key_env="LOCAL_MODEL",
        supports_builtin_live=True,
        builtin_runtime_adapter="local",
        live_title="local - built-in Ollama adapter",
        demo_title="local - demo or built-in Ollama adapter",
        include_in_env_detection=False,
        prefer_live_when_selected=True,
    ),
    "custom_api": ProviderRuntimeCapability(
        name="custom_api",
        default_api_key_env="CUSTOM_API_KEY",
        supports_builtin_live=True,
        builtin_runtime_adapter="custom_api",
        live_title="custom_api - Responses-compatible gateway",
        demo_title="custom_api - demo or gateway-backed live adapter",
        include_in_env_detection=False,
    ),
}

DEFAULT_PROVIDER_RUNTIME_CAPABILITY = ProviderRuntimeCapability(
    name="unknown",
    default_api_key_env="MODEL_TOKEN",
    supports_builtin_live=False,
    builtin_runtime_adapter=None,
    live_title="unknown - requires custom module adapter",
    demo_title="unknown - demo only unless you bring a custom adapter",
    include_in_env_detection=False,
)


def provider_runtime_capability(provider: str) -> ProviderRuntimeCapability:
    clean_provider = (provider or "").strip().lower()
    return PROVIDER_RUNTIME_CAPABILITIES.get(clean_provider, DEFAULT_PROVIDER_RUNTIME_CAPABILITY)


def default_api_key_env(provider: str) -> str:
    return provider_runtime_capability(provider).default_api_key_env


def supports_builtin_live(provider: str) -> bool:
    return provider_runtime_capability(provider).supports_builtin_live


def builtin_runtime_adapter(provider: str) -> str | None:
    return provider_runtime_capability(provider).builtin_runtime_adapter


def default_provider_from_env(environ: Mapping[str, str] | None = None) -> str:
    env = environ or os.environ
    configured = [
        provider
        for provider in PROVIDER_CHOICES
        if provider_runtime_capability(provider).include_in_env_detection
        and env.get(default_api_key_env(provider))
    ]
    if not configured:
        return "local"
    if len(configured) == 1:
        return configured[0]
    if "openai" in configured:
        return "openai"
    return configured[0]
