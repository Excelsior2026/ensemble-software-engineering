"""Interactive wizard for creating ese.config.yaml."""

from __future__ import annotations

from typing import Any, Dict

import questionary

from ese.config import write_config

PROVIDER_CHOICES = [
    "openai",
    "anthropic",
    "google",
    "xai",
    "openrouter",
    "huggingface",
    "local",
    "custom_api",
]

COMMON_MODELS_BY_PROVIDER: Dict[str, list[str]] = {
    "openai": [
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
        "o3",
    ],
    "anthropic": [
        "claude-sonnet-4",
        "claude-opus-4",
    ],
    "google": [
        "gemini-2.0-flash",
        "gemini-2.0-pro",
    ],
    "xai": [
        "grok-3",
        "grok-3-mini",
    ],
    "openrouter": [
        "openai/gpt-5",
        "anthropic/claude-sonnet-4",
        "google/gemini-2.0-pro",
    ],
    "huggingface": [
        "meta-llama/Llama-3.3-70B-Instruct",
        "Qwen/Qwen2.5-Coder-32B-Instruct",
    ],
    "local": [
        "llama3.1:8b",
        "qwen2.5-coder:14b",
    ],
}

CUSTOM_MODEL_CHOICE = "custom (type model id)"

ROLE_DESCRIPTIONS: Dict[str, str] = {
    "architect": "System design, decomposition, and interface contracts.",
    "implementer": "Code changes and refactors.",
    "adversarial_reviewer": "Bug/risk hunting and regression checks.",
    "security_auditor": "Threat modeling and vulnerability review.",
    "test_generator": "Unit/integration/e2e test generation.",
    "performance_analyst": "Latency, memory, and scalability analysis.",
    "documentation_writer": "README, API docs, and migration notes.",
    "devops_sre": "CI/CD, deploy safety, and observability.",
    "database_engineer": "Schema/index/migration correctness.",
    "release_manager": "Go/no-go risk assessment and rollout checks.",
}

DEFAULT_SELECTED_ROLES = [
    "architect",
    "implementer",
    "adversarial_reviewer",
    "security_auditor",
    "test_generator",
    "performance_analyst",
]

DEFAULT_DISALLOW_SAME_MODEL_PAIRS = [
    ("architect", "implementer"),
    ("implementer", "adversarial_reviewer"),
    ("implementer", "security_auditor"),
    ("implementer", "release_manager"),
]

ROLE_DEFAULTS_BY_PRESET: Dict[str, Dict[str, Dict[str, Any]]] = {
    "fast": {
        "architect": {"temperature": 0.2},
        "implementer": {"temperature": 0.1},
        "adversarial_reviewer": {"temperature": 0.6},
        "security_auditor": {"temperature": 0.2},
        "test_generator": {"temperature": 0.2},
        "performance_analyst": {"temperature": 0.2},
        "documentation_writer": {"temperature": 0.3},
        "devops_sre": {"temperature": 0.2},
        "database_engineer": {"temperature": 0.2},
        "release_manager": {"temperature": 0.2},
    },
    "balanced": {
        "architect": {"temperature": 0.2},
        "implementer": {"temperature": 0.1},
        "adversarial_reviewer": {"temperature": 0.7},
        "security_auditor": {"temperature": 0.2},
        "test_generator": {"temperature": 0.2},
        "performance_analyst": {"temperature": 0.2},
        "documentation_writer": {"temperature": 0.2},
        "devops_sre": {"temperature": 0.2},
        "database_engineer": {"temperature": 0.2},
        "release_manager": {"temperature": 0.2},
    },
    "strict": {
        "architect": {"temperature": 0.1},
        "implementer": {"temperature": 0.05},
        "adversarial_reviewer": {"temperature": 0.6},
        "security_auditor": {"temperature": 0.1},
        "test_generator": {"temperature": 0.1},
        "performance_analyst": {"temperature": 0.1},
        "documentation_writer": {"temperature": 0.15},
        "devops_sre": {"temperature": 0.1},
        "database_engineer": {"temperature": 0.1},
        "release_manager": {"temperature": 0.1},
    },
    "paranoid": {
        "architect": {"temperature": 0.1},
        "implementer": {"temperature": 0.05},
        "adversarial_reviewer": {"temperature": 0.8},
        "security_auditor": {"temperature": 0.1},
        "test_generator": {"temperature": 0.1},
        "performance_analyst": {"temperature": 0.1},
        "documentation_writer": {"temperature": 0.15},
        "devops_sre": {"temperature": 0.1},
        "database_engineer": {"temperature": 0.1},
        "release_manager": {"temperature": 0.1},
    },
}


def _default_api_key_env(provider: str) -> str:
    if provider == "openai":
        return "OPENAI_API_KEY"
    if provider == "anthropic":
        return "ANTHROPIC_API_KEY"
    if provider == "google":
        return "GOOGLE_API_KEY"
    if provider == "xai":
        return "XAI_API_KEY"
    if provider == "openrouter":
        return "OPENROUTER_API_KEY"
    if provider == "huggingface":
        return "HF_TOKEN"
    if provider == "local":
        return "LOCAL_MODEL"
    if provider == "custom_api":
        return "CUSTOM_API_KEY"
    return "MODEL_TOKEN"


def _select_default_model(provider: str) -> str:
    common_models = COMMON_MODELS_BY_PROVIDER.get(provider, [])
    if not common_models:
        return questionary.text(
            "Default model name (provider model id):",
        ).ask()

    model_choice = questionary.select(
        "Default model:",
        choices=common_models + [CUSTOM_MODEL_CHOICE],
    ).ask()

    if model_choice == CUSTOM_MODEL_CHOICE:
        return questionary.text(
            "Custom model id:",
        ).ask()

    return model_choice


def _select_runtime_adapter(provider: str) -> str:
    adapter_choices = ["dry-run"]
    if provider == "openai":
        adapter_choices.append("openai")
    adapter_choices.append("custom module:function")

    adapter_choice = questionary.select(
        "Runtime adapter:",
        choices=adapter_choices,
    ).ask()

    if adapter_choice == "custom module:function":
        return questionary.text(
            "Custom adapter reference (module:function):",
        ).ask()
    return adapter_choice


def _role_choices() -> list[questionary.Choice]:
    return [
        questionary.Choice(
            title=f"{role} - {description}",
            value=role,
            checked=role in DEFAULT_SELECTED_ROLES,
        )
        for role, description in ROLE_DESCRIPTIONS.items()
    ]


def _ordered_selected_roles(selected_roles: list[str]) -> list[str]:
    selected = set(selected_roles)
    return [role for role in ROLE_DESCRIPTIONS if role in selected]


def _roles_for_preset(preset: str, selected_roles: list[str]) -> Dict[str, Dict[str, Any]]:
    defaults = ROLE_DEFAULTS_BY_PRESET.get(preset, {})
    return {role: dict(defaults.get(role, {"temperature": 0.2})) for role in selected_roles}


def _ensemble_constraints(selected_roles: list[str]) -> Dict[str, Any]:
    selected = set(selected_roles)
    pairs = [
        [left, right]
        for left, right in DEFAULT_DISALLOW_SAME_MODEL_PAIRS
        if left in selected and right in selected
    ]
    return {"disallow_same_model_pairs": pairs}


def run_wizard(config_path: str = "ese.config.yaml") -> str:
    mode = questionary.select("Setup mode:", choices=["ensemble", "solo"]).ask()
    provider = questionary.select(
        "Provider:",
        choices=PROVIDER_CHOICES,
    ).ask()

    provider_name = provider
    provider_cfg: Dict[str, Any] = {}

    if provider == "custom_api":
        provider_name = questionary.text(
            "Custom provider name (e.g., my-gateway):",
        ).ask()
        custom_base_url = questionary.text(
            "Custom API base URL (optional):",
        ).ask()
        if custom_base_url:
            provider_cfg["base_url"] = custom_base_url

    model = _select_default_model(provider)
    api_key_env = questionary.text(
        "API key environment variable:",
        default=_default_api_key_env(provider),
    ).ask()
    runtime_adapter = _select_runtime_adapter(provider)

    preset = questionary.select(
        "Preset:", choices=["fast", "balanced", "strict", "paranoid"],
    ).ask()

    selected_roles = questionary.checkbox(
        "Select roles for this pipeline (each option includes its responsibility):",
        choices=_role_choices(),
        validate=lambda value: bool(value) or "Select at least one role.",
    ).ask()
    selected_roles = _ordered_selected_roles(selected_roles or DEFAULT_SELECTED_ROLES)

    enforce_json = questionary.confirm(
        "Enforce JSON-only outputs for role reports?",
        default=True,
    ).ask()

    fail_on_high = questionary.confirm(
        "Fail pipeline on HIGH severity findings?",
        default=True,
    ).ask()

    cfg: Dict[str, Any] = {
        "version": 1,
        "mode": mode,
        "provider": {
            "name": provider_name,
            "model": model,
            "api_key_env": api_key_env,
            **provider_cfg,
        },
        "preset": preset,
        "roles": _roles_for_preset(preset=preset, selected_roles=selected_roles),
        "output": {
            "artifacts_dir": "artifacts",
            "enforce_json": enforce_json,
        },
        "gating": {
            "fail_on_high": fail_on_high,
        },
        "runtime": {
            "adapter": runtime_adapter,
            "timeout_seconds": 60,
            "max_retries": 2,
            "retry_backoff_seconds": 1.0,
        },
    }

    if runtime_adapter == "openai":
        cfg["runtime"]["openai"] = {
            "base_url": provider_cfg.get("base_url", "https://api.openai.com/v1"),
        }

    if mode == "ensemble":
        cfg["constraints"] = _ensemble_constraints(selected_roles=selected_roles)

    write_config(config_path, cfg)
    return config_path
