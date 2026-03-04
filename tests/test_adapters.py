from __future__ import annotations

import json

import pytest

from ese.adapters import AdapterExecutionError, custom_api_adapter, openai_adapter


class _FakeResponse:
    def __init__(self, body: str) -> None:
        self._body = body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def read(self) -> bytes:
        return self._body.encode("utf-8")


def _openai_cfg() -> dict:
    return {
        "provider": {
            "name": "openai",
            "model": "gpt-5-mini",
            "api_key_env": "OPENAI_API_KEY",
        },
        "runtime": {
            "adapter": "openai",
            "timeout_seconds": 30,
            "max_retries": 0,
            "retry_backoff_seconds": 0.1,
            "openai": {"base_url": "https://api.openai.com/v1"},
        },
    }


def test_openai_adapter_allows_zero_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _openai_cfg()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(AdapterExecutionError) as exc:
        openai_adapter(
            role="architect",
            model="openai:gpt-5-mini",
            prompt="test",
            context={},
            cfg=cfg,
        )

    message = str(exc.value)
    assert "Missing API key in env var 'OPENAI_API_KEY'" in message
    assert "max_retries" not in message


def test_custom_api_adapter_requires_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = {
        "provider": {
            "name": "my-gateway",
            "model": "my-model",
            "api_key_env": "CUSTOM_GATEWAY_TOKEN",
        },
        "runtime": {
            "adapter": "custom_api",
            "timeout_seconds": 30,
            "max_retries": 0,
            "retry_backoff_seconds": 0.1,
        },
    }
    monkeypatch.setenv("CUSTOM_GATEWAY_TOKEN", "token")

    with pytest.raises(AdapterExecutionError) as exc:
        custom_api_adapter(
            role="architect",
            model="my-gateway:my-model",
            prompt="test",
            context={},
            cfg=cfg,
        )

    assert "Custom API base URL is required" in str(exc.value)


def test_custom_api_adapter_success(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = {
        "provider": {
            "name": "my-gateway",
            "model": "my-model",
            "api_key_env": "CUSTOM_GATEWAY_TOKEN",
            "base_url": "https://gateway.example/v1",
        },
        "runtime": {
            "adapter": "custom_api",
            "timeout_seconds": 30,
            "max_retries": 0,
            "retry_backoff_seconds": 0.1,
            "custom_api": {"base_url": "https://gateway.example/v1"},
        },
    }

    monkeypatch.setenv("CUSTOM_GATEWAY_TOKEN", "token")

    def _fake_urlopen(request, timeout):  # noqa: ANN001
        assert timeout == 30
        assert request.full_url == "https://gateway.example/v1/responses"
        payload = json.loads(request.data.decode("utf-8"))
        assert payload["model"] == "my-model"
        return _FakeResponse(json.dumps({"output_text": "ok"}))

    monkeypatch.setattr("ese.adapters.urllib.request.urlopen", _fake_urlopen)

    output = custom_api_adapter(
        role="architect",
        model="my-gateway:my-model",
        prompt="test prompt",
        context={},
        cfg=cfg,
    )

    assert output == "ok"
