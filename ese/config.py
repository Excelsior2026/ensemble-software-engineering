"""ESE configuration loading, validation, and helper utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

CONFIG_VERSION = 1


class ConfigValidationError(ValueError):
    """Raised when ESE configuration is malformed or unsupported."""


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    model: str
    api_key_env: str | None = None
    base_url: str | None = None

    @field_validator("name", "model")
    @classmethod
    def _must_be_non_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must be a non-empty string")
        return cleaned

    @field_validator("api_key_env", "base_url")
    @classmethod
    def _optional_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must be a non-empty string")
        return cleaned


class RoleConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    provider: str | None = None
    model: str | None = None
    temperature: float | None = None


class ConstraintsConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    disallow_same_model_pairs: list[tuple[str, str]] = Field(default_factory=list)

    @field_validator("disallow_same_model_pairs", mode="before")
    @classmethod
    def _normalize_pairs(cls, value: Any) -> list[tuple[str, str]]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("must be a list of role name pairs")

        pairs: list[tuple[str, str]] = []
        for pair in value:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError("each pair must contain exactly two role names")

            left, right = pair
            if not isinstance(left, str) or not isinstance(right, str):
                raise ValueError("pair entries must be strings")

            left = left.strip()
            right = right.strip()
            if not left or not right:
                raise ValueError("pair entries must be non-empty strings")

            pairs.append((left, right))
        return pairs


class OutputConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    artifacts_dir: str = "artifacts"
    enforce_json: bool = True


class GatingConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    fail_on_high: bool = True


class OpenAIRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    base_url: str = "https://api.openai.com/v1"

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must be a non-empty string")
        return cleaned


class CustomAPIRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    base_url: str | None = None

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must be a non-empty string")
        return cleaned


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    adapter: str = "dry-run"
    timeout_seconds: float = 60.0
    max_retries: int = 2
    retry_backoff_seconds: float = 1.0
    max_output_tokens: int | None = None
    openai: OpenAIRuntimeConfig = Field(default_factory=OpenAIRuntimeConfig)
    custom_api: CustomAPIRuntimeConfig | None = None

    @field_validator("adapter")
    @classmethod
    def _validate_adapter(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must be a non-empty string")
        return cleaned

    @field_validator("timeout_seconds", "retry_backoff_seconds")
    @classmethod
    def _validate_positive_float(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("must be > 0")
        return value

    @field_validator("max_retries")
    @classmethod
    def _validate_non_negative_retries(cls, value: int) -> int:
        if value < 0:
            raise ValueError("must be >= 0")
        return value

    @field_validator("max_output_tokens")
    @classmethod
    def _validate_optional_tokens(cls, value: int | None) -> int | None:
        if value is None:
            return value
        if value <= 0:
            raise ValueError("must be > 0")
        return value


class ESEConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: int = CONFIG_VERSION
    mode: Literal["ensemble", "solo"] = "ensemble"
    provider: ProviderConfig
    roles: dict[str, RoleConfig] = Field(default_factory=dict)
    constraints: ConstraintsConfig = Field(default_factory=ConstraintsConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    gating: GatingConfig = Field(default_factory=GatingConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: int) -> int:
        if value != CONFIG_VERSION:
            raise ValueError(
                f"unsupported version {value}; expected {CONFIG_VERSION}",
            )
        return value

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, value: Any) -> str:
        cleaned = str(value or "ensemble").strip().lower()
        if cleaned not in {"ensemble", "solo"}:
            raise ValueError("must be either 'ensemble' or 'solo'")
        return cleaned

    @model_validator(mode="after")
    def _validate_adapter_contract(self) -> "ESEConfig":
        adapter = self.runtime.adapter.strip().lower()
        if adapter != "custom_api":
            return self

        provider_name = self.provider.name.strip().lower()
        if provider_name == "openai":
            raise ValueError("runtime.adapter=custom_api requires provider.name to be a custom provider")
        if not self.provider.api_key_env:
            raise ValueError("runtime.adapter=custom_api requires provider.api_key_env")

        provider_base_url = self.provider.base_url
        runtime_base_url = self.runtime.custom_api.base_url if self.runtime.custom_api else None
        if not provider_base_url and not runtime_base_url:
            raise ValueError(
                "runtime.adapter=custom_api requires provider.base_url or runtime.custom_api.base_url",
            )
        return self


def _raise_validation_error(source: str, err: ValidationError) -> None:
    details: list[str] = []
    for item in err.errors():
        loc = ".".join(str(part) for part in item.get("loc", [])) or "<root>"
        msg = item.get("msg", "invalid value")
        details.append(f"{loc}: {msg}")

    detail_text = "; ".join(details)
    raise ConfigValidationError(f"Invalid ESE config at {source}: {detail_text}") from err


def validate_config(cfg: Dict[str, Any], source: str = "<memory>") -> Dict[str, Any]:
    """Validate and normalize an ESE config dictionary."""
    try:
        model = ESEConfig.model_validate(cfg or {})
    except ValidationError as err:
        _raise_validation_error(source, err)
    return model.model_dump(mode="python", exclude_none=True)


def load_config(path: str, validate: bool = True) -> Dict[str, Any]:
    """Load YAML config into a dict, optionally schema-validating it."""
    p = Path(path)
    if not p.exists():
        if validate:
            raise ConfigValidationError(f"Config file not found: {path}")
        return {}

    with p.open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}

    if not isinstance(loaded, dict):
        raise ConfigValidationError(f"Invalid ESE config at {path}: root must be a mapping")

    if not validate:
        return loaded

    return validate_config(loaded, source=path)


def write_config(path: str, cfg: Dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)


def resolve_role_model(cfg: Dict[str, Any], role: str) -> str:
    """Resolve effective model identifier for a role."""
    roles_cfg: Dict[str, Any] = cfg.get("roles", {}) or {}
    role_cfg: Dict[str, Any] = roles_cfg.get(role, {}) or {}

    provider_cfg: Dict[str, Any] = cfg.get("provider", {}) or {}
    provider = provider_cfg.get("name", "unknown")
    model = provider_cfg.get("model", "unknown")

    if "provider" in role_cfg and role_cfg.get("provider"):
        provider = role_cfg["provider"]

    if "model" in role_cfg and role_cfg.get("model"):
        model = role_cfg["model"]

    return f"{provider}:{model}"
